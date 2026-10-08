"""Hiring and removing people, across several departments.

* Hire     a head, co-head, team lead, employee or intern (or any custom role) into a department, and optionally give
           them a role in other departments at the same time.
* Roles    add or remove someone's role in another department later (their "also works in" roles).
* Remove   take someone off the team: their account is switched off, their open work is handed to a colleague you
           choose, their other-department roles end, and the removal is recorded.
* Pause    keep someone on the team but leave them out of every "Assign to" list.

Who may do what follows the same rules as the rest of HIMS: you can only create, change or remove people who rank
below you, department heads work inside their own department, and administrators work across all of them.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from ..constants import (CAMPAIGN_OPEN, CONTENT_OPEN, PROJECT_OPEN, REMOVAL_REASONS, REQUEST_OPEN, TASK_OPEN, TICKET_OPEN)
from ..extensions import db
from ..models import (Campaign, Candidate, ContentRequest, Department, InternalRequest, Lead, Project, RemovalRecord, Role,
                      Startup, Task, Ticket, User, UserDepartmentRole)
from ..scopes import assignable_users, can_manage_user
from ..security import access, has_perm
from ..services import add_comment, assign_employee_code, log_activity, notify
from ..timeutil import today
from .employees import _assignable_roles, _can_add, _email_taken, _form as employee_form, temp_password

bp = Blueprint("people", __name__, url_prefix="/people")

EXTRA_ROWS = 3


# ---------------------------------------------------------------- rules shared with the profile page
def departments_actor_may_staff(actor):
    """Departments this person may put someone into: all of them for admins, led departments for heads."""
    if has_perm(actor, "employees.manage"):
        return Department.query.filter_by(active=True).order_by(Department.name).all()
    if has_perm(actor, "employees.manage_team") or has_perm(actor, "employees.departments"):
        return [d for d in Department.query.filter_by(active=True).order_by(Department.name).all() if actor.manages_department(d.id)]
    return []


def _roles_below(actor):
    return _assignable_roles(actor)


def can_grant_department_role(actor, person, dept, role):
    if person.id == actor.id or not (has_perm(actor, "employees.manage") or has_perm(actor, "employees.departments")):
        return False
    if not actor.is_super and role.level >= actor.role.level:
        return False
    if has_perm(actor, "employees.manage"):
        return actor.is_super or person.role.level < actor.role.level
    return actor.manages_department(dept.id) and person.role.level < actor.role.level


def can_revoke_department_role(actor, link):
    if link.user_id == actor.id or not (has_perm(actor, "employees.manage") or has_perm(actor, "employees.departments")):
        return False
    if has_perm(actor, "employees.manage"):
        return actor.is_super or link.user.role.level < actor.role.level
    return actor.manages_department(link.department_id) and link.role.level < actor.role.level


def other_active_super_admins(person):
    return User.query.join(Role, User.role_id == Role.id).filter(Role.key == "super_admin", User.active.is_(True), User.id != person.id).count()


def can_remove(actor, person):
    return bool(has_perm(actor, "employees.remove") and person.id != actor.id and person.active
                and can_manage_user(actor, person))


def can_pause(actor, person):
    return bool(person.id != actor.id and person.active
                and (can_manage_user(actor, person) or (actor.manages_department(person.department_id) and person.role.level < actor.role.level)))


def people_actions(actor, person):
    """Everything the profile page needs to show the staffing buttons for this person."""
    depts = [d for d in departments_actor_may_staff(actor) if d.id != person.department_id]
    return {
        "can_remove": can_remove(actor, person), "can_pause": can_pause(actor, person), "paused": not person.task_assignable,
        "can_roles": person.id != actor.id and bool(depts) and (has_perm(actor, "employees.manage") or has_perm(actor, "employees.departments")),
        "dept_options": [(d.id, d.name) for d in depts], "role_options": [(r.id, r.name) for r in _roles_below(actor)],
        "can_revoke": lambda link: can_revoke_department_role(actor, link),
    }


# ---------------------------------------------------------------- hire
def _extra_rows(actor, primary_dept_id, form_data):
    """Read and validate the 'also works in' rows of the hire form. Returns (rows, error)."""
    allowed = {d.id for d in departments_actor_may_staff(actor)}
    roles = {r.id: r for r in _roles_below(actor)}
    rows, seen = [], {primary_dept_id}
    for n in range(1, EXTRA_ROWS + 1):
        d_raw, r_raw = form_data.get(f"extra_dept_{n}", "").strip(), form_data.get(f"extra_role_{n}", "").strip()
        if not d_raw and not r_raw:
            continue
        if not (d_raw.isdigit() and r_raw.isdigit()):
            return rows, f"Extra department {n}: choose both a department and a role, or clear both."
        dept_id, role_id = int(d_raw), int(r_raw)
        if dept_id not in allowed or role_id not in roles:
            return rows, f"Extra department {n}: you are not allowed to give that role in that department."
        if dept_id in seen:
            return rows, f"Extra department {n}: that department is already used for this person."
        seen.add(dept_id)
        rows.append((dept_id, role_id))
    return rows, None


@bp.route("/hire", methods=["GET", "POST"])
@access()
def hire():
    if not _can_add(current_user):
        abort(403)
    full = has_perm(current_user, "employees.manage")
    form = employee_form(current_user)
    depts = departments_actor_may_staff(current_user)
    roles = _roles_below(current_user)
    prefill = request.args.get("dept", "")
    if request.method == "GET" and prefill:
        match = next((d for d in depts if d.slug == prefill), None)
        if match:
            next(f for f in form.fields if f.name == "department_id").value = match.id
    extras = [{"dept": request.form.get(f"extra_dept_{n}", ""), "role": request.form.get(f"extra_role_{n}", "")} for n in range(1, EXTRA_ROWS + 1)]
    extra_error = None
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        primary = d["department_id"] if full else current_user.department_id
        rows, extra_error = _extra_rows(current_user, primary, request.form)
        if _email_taken(d["email"]):
            form.fields[1].error = "Another employee already uses this email."
        elif extra_error is None:
            role = db.session.get(Role, d["role_id"])
            if role.level >= current_user.role.level and not current_user.is_super:
                abort(403)
            user = User(name=d["name"], email=d["email"], job_title=d["job_title"], phone=d["phone"], department_id=primary,
                        role_id=role.id, joined_on=d["joined_on"] or today(), must_change_password=True,
                        employment_type=d["employment_type"], reports_to_id=d["reports_to_id"])
            password = d["password"] or temp_password()
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            assign_employee_code(user)
            names = []
            for dept_id, role_id in rows:
                db.session.add(UserDepartmentRole(user_id=user.id, department_id=dept_id, role_id=role_id))
                names.append(f"{db.session.get(Role, role_id).name} in {db.session.get(Department, dept_id).name}")
            log_activity("created", "user", user.id, f"Hired {user.name} as {role.name}"
                         + (f" in {user.department.name}" if user.department else "") + (f"; also {', '.join(names)}" if names else ""))
            notify(user, "Welcome to HIMS", "Your account is ready. Please review your profile.", url_for("employees.profile", uid=user.id))
            db.session.commit()
            flash(f"{user.name} was hired as {role.name}. Temporary password: {password}  (shown once, share it privately).", "success")
            return redirect(url_for("employees.profile", uid=user.id))
    return render_template("people/hire.html", form=form, full=full, depts=[(x.id, x.name) for x in depts],
                           roles=[(r.id, r.name) for r in roles], extras=extras, extra_error=extra_error)


# ---------------------------------------------------------------- roles in other departments
@bp.route("/<int:uid>/departments", methods=["POST"])
@access()
def add_department_role(uid):
    person = User.query.get_or_404(uid)
    back = url_for("employees.profile", uid=person.id) + "#departments"
    dept = db.session.get(Department, request.form.get("department_id", type=int) or 0)
    role = db.session.get(Role, request.form.get("role_id", type=int) or 0)
    if dept is None or role is None:
        flash("Choose a department and a role.", "error")
        return redirect(back)
    if not can_grant_department_role(current_user, person, dept, role):
        abort(403)
    if dept.id == person.department_id:
        flash(f"{dept.name} is already {person.name}'s main department. Change their role from Edit instead.", "error")
        return redirect(back)
    link = UserDepartmentRole.query.filter_by(user_id=person.id, department_id=dept.id).first()
    if link:
        link.role_id = role.id
        verb = "changed to"
    else:
        db.session.add(UserDepartmentRole(user_id=person.id, department_id=dept.id, role_id=role.id))
        verb = "added as"
    log_activity("updated", "user", person.id, f"{person.name} {verb} {role.name} in {dept.name}")
    notify(person, "You were given a role in another department", f"You are now {role.name} in {dept.name}.",
           url_for("employees.profile", uid=person.id))
    db.session.commit()
    flash(f"{person.name} is now {role.name} in {dept.name}.", "success")
    return redirect(back)


@bp.route("/<int:uid>/departments/<int:lid>/remove", methods=["POST"])
@access()
def remove_department_role(uid, lid):
    link = UserDepartmentRole.query.filter_by(id=lid, user_id=uid).first_or_404()
    if not can_revoke_department_role(current_user, link):
        abort(403)
    person, dept, role = link.user, link.department, link.role
    db.session.delete(link)
    log_activity("updated", "user", person.id, f"Removed {person.name} from the {role.name} role in {dept.name}")
    notify(person, "A department role ended", f"You are no longer {role.name} in {dept.name}.", url_for("employees.profile", uid=person.id))
    db.session.commit()
    flash(f"{person.name} no longer works in {dept.name}.", "success")
    return redirect(url_for("employees.profile", uid=person.id) + "#departments")


# ---------------------------------------------------------------- pause or resume task assignment
@bp.route("/<int:uid>/assignment", methods=["POST"])
@access()
def toggle_assignment(uid):
    person = User.query.get_or_404(uid)
    if not can_pause(current_user, person):
        abort(403)
    person.task_assignable = not person.task_assignable
    log_activity("updated", "user", person.id, f"{'Resumed' if person.task_assignable else 'Paused'} task assignment for {person.name}")
    db.session.commit()
    flash(f"{person.name} {'can be given tasks again.' if person.task_assignable else 'will no longer appear in any Assign to list. Their existing tasks are unchanged.'}", "success")
    return redirect(url_for("employees.profile", uid=person.id))


# ---------------------------------------------------------------- remove from the team
def _open_work(person):
    """Everything still open that belongs to this person: {label: [rows]} plus a flat count."""
    return {
        "Tasks": Task.query.filter(Task.assignee_id == person.id, Task.status.in_(TASK_OPEN)).all(),
        "Leads": Lead.query.filter(Lead.owner_id == person.id, Lead.stage.not_in(("won", "lost"))).all(),
        "Candidates": Candidate.query.filter(Candidate.owner_id == person.id, Candidate.stage.not_in(("hired", "rejected"))).all(),
        "Startup applications": Startup.query.filter(Startup.owner_id == person.id, Startup.stage.not_in(("graduated", "rejected"))).all(),
        "Helpdesk tickets": Ticket.query.filter(Ticket.assignee_id == person.id, Ticket.status.in_(TICKET_OPEN)).all(),
        "Design and content requests": ContentRequest.query.filter(ContentRequest.assignee_id == person.id, ContentRequest.status.in_(CONTENT_OPEN)).all(),
        "Department requests": InternalRequest.query.filter(InternalRequest.assignee_id == person.id, InternalRequest.status.in_(REQUEST_OPEN)).all(),
        "Projects they manage": Project.query.filter(Project.manager_id == person.id, Project.status.in_(PROJECT_OPEN)).all(),
        "Campaigns they own": Campaign.query.filter(Campaign.owner_id == person.id, Campaign.status.in_(CAMPAIGN_OPEN)).all(),
    }


def _handover_candidates(actor, person):
    pool = {u.id: u for u in assignable_users(actor)}
    pool[actor.id] = actor
    if person.reports_to is not None:
        pool[person.reports_to.id] = person.reports_to
    if person.department is not None:
        for u in person.department.heads + person.department.co_heads:
            pool[u.id] = u
    pool.pop(person.id, None)
    return sorted((u for u in pool.values() if u.active and u.task_assignable), key=lambda u: u.name)


@bp.route("/<int:uid>/remove", methods=["GET", "POST"])
@access()
def remove(uid):
    person = User.query.get_or_404(uid)
    if not can_remove(current_user, person):
        abort(403)
    if person.is_super and other_active_super_admins(person) == 0:
        flash("This is the last active Super Admin. Create another Super Admin first, otherwise nobody could manage HIMS.", "error")
        return redirect(url_for("employees.profile", uid=person.id))
    work = _open_work(person)
    counts = {k: len(v) for k, v in work.items() if v}
    candidates = _handover_candidates(current_user, person)
    errors, values = {}, {"reason": "Resigned", "note": "", "handover": "", "tasks": "handover"}
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k in values}
        target = next((u for u in candidates if str(u.id) == values["handover"]), None)
        cancel_tasks = values["tasks"] == "cancel"
        needs_target = any(n for k, n in counts.items() if not (k == "Tasks" and cancel_tasks)) or bool(person.direct_reports)
        if values["reason"] not in REMOVAL_REASONS:
            errors["reason"] = "Choose a reason."
        if needs_target and target is None:
            errors["handover"] = "Choose who takes over this person's open work and direct reports."
        if not errors:
            moved = 0
            for t in work["Tasks"]:
                if cancel_tasks:
                    t.status = "cancelled"
                    add_comment("task", t.id, f"Cancelled because {person.name} left the team." + (f"\n{values['note']}" if values["note"] else ""), kind="status")
                else:
                    t.assignee_id = target.id
                    add_comment("task", t.id, f"Reassigned from {person.name} to {target.name} because {person.name} left the team.", kind="status")
                moved += 1
            for key, attr in (("Leads", "owner_id"), ("Candidates", "owner_id"), ("Startup applications", "owner_id"),
                              ("Helpdesk tickets", "assignee_id"), ("Design and content requests", "assignee_id"),
                              ("Department requests", "assignee_id"), ("Projects they manage", "manager_id"), ("Campaigns they own", "owner_id")):
                for row in work[key]:
                    setattr(row, attr, target.id)
                    moved += 1
            new_boss = person.reports_to_id or (target.id if target is not None and target.is_manager_level else current_user.id)
            for u in list(person.direct_reports):
                u.reports_to_id = new_boss if new_boss != u.id else None
            for link in list(person.secondary_roles):
                db.session.delete(link)
            person.active = False
            person.task_assignable = False
            db.session.add(RemovalRecord(user_id=person.id, removed_by_id=current_user.id, department_id=person.department_id,
                                         role_name=person.title_label, reason=values["reason"], note=values["note"][:400] or None,
                                         handover_to_id=target.id if target else None, items_moved=moved))
            log_activity("removed", "user", person.id, f"Removed {person.name} ({person.title_label}) from the team: {values['reason']}; "
                         f"{moved} open item(s) {'handed to ' + target.name if target else 'closed'}")
            if target is not None and moved:
                notify(target, "Work handed over to you", f"{moved} open item(s) from {person.name} are now yours.", url_for("tasks.index"))
            db.session.commit()
            flash(f"{person.name} was removed from the team. Their account is switched off and {moved} open item(s) were "
                  f"{'handed to ' + target.name if target else 'closed'}. Their history is kept; you can reactivate them from their profile.", "success")
            return redirect(url_for("employees.profile", uid=person.id))
    return render_template("people/remove.html", person=person, counts=counts, total=sum(counts.values()), candidates=candidates,
                           reasons=REMOVAL_REASONS, values=values, errors=errors, reports=[u for u in person.direct_reports if u.active],
                           links=person.secondary_roles)


@bp.route("/removed")
@access()
def removed():
    if not (has_perm(current_user, "employees.remove") or has_perm(current_user, "employees.manage")):
        abort(403)
    q = RemovalRecord.query
    if not current_user.is_admin_level:
        q = q.filter(RemovalRecord.department_id.in_(current_user.led_department_ids or [0]))
    rows = q.order_by(RemovalRecord.created_at.desc()).limit(200).all()
    return render_template("people/removed.html", rows=rows)
