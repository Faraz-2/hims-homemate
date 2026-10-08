import csv
import io
import re
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..constants import EMPLOYMENT_TYPES, TASK_OPEN
from ..extensions import db
from ..forms import Field, Form
from ..models import Attendance, Department, DocumentType, EmployeeDocument, LeaveRequest, Permission, Role, Task, User, UserPermission
from ..scopes import can_manage_user, promotion_options
from ..security import access, has_perm
from ..services import assign_employee_code, csv_response, get_int, log_activity, notify, paginate, save_upload, upload_path
from ..timeutil import now, today

bp = Blueprint("employees", __name__, url_prefix="/employees")

_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temp_password() -> str:
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _assignable_roles(actor):
    q = Role.query.order_by(Role.level.desc())
    return q.all() if actor.is_super else q.filter(Role.level < actor.role.level).all()


def _can_add(actor):
    """Admins add anyone; department heads add people to their own department."""
    return has_perm(actor, "employees.manage") or has_perm(actor, "employees.manage_team")


def _line_managers(actor, person):
    """People who can be someone's line manager: team lead level and above."""
    q = User.query.filter_by(active=True).join(Role, User.role_id == Role.id).filter(Role.level >= 40)
    if not has_perm(actor, "employees.manage"):          # a head chooses managers from their own department
        q = q.filter(User.department_id == actor.department_id)
    if person is not None:
        q = q.filter(User.id != person.id)
    return q.order_by(User.name).all()


def _creates_cycle(person, manager_id):
    """True if making manager_id the person's manager would put someone above themselves."""
    node, hops = db.session.get(User, manager_id) if manager_id else None, 0
    while node is not None and hops < 25:
        if node.id == person.id:
            return True
        node, hops = node.reports_to, hops + 1
    return False


def _form(actor, person=None):
    full = has_perm(actor, "employees.manage")
    depts = (Department.query.filter_by(active=True).order_by(Department.name).all() if full
             else ([actor.department] if actor.department else []))
    fields = [
        Field("name", "Full name", required=True),
        Field("email", "Work email", "email", required=True),
        Field("job_title", "Job title"),
        Field("phone", "Phone", "tel"),
        Field("department_id", "Department", "select", options=[(d.id, d.name) for d in depts], coerce=int,
              blank_label="No department", required=not full,
              help=None if full else "You can add people to your own department."),
    ]
    if person is None or person.id != actor.id:
        fields.append(Field("role_id", "Role", "select", options=[(r.id, r.name) for r in _assignable_roles(actor)],
                            coerce=int, required=True, help="The role decides what this person can do. Fine-tune it later under Rights."))
    fields.append(Field("employment_type", "Employment type", "select", options=EMPLOYMENT_TYPES, required=True, value="full_time"))
    fields.append(Field("reports_to_id", "Reports to (line manager)", "select", coerce=int, blank_label="No line manager",
                        options=[(u.id, f"{u.name} ({u.department.name if u.department else 'No department'})") for u in _line_managers(actor, person)],
                        help="Managers can give tasks to the people who report to them."))
    fields.append(Field("joined_on", "Joined on", "date"))
    if person is None:
        fields.append(Field("password", "Initial password", "password", min_length=8, half=False,
                            help="Leave empty to generate a temporary password. The employee must change it at first sign-in."))
    return Form(fields)


def _email_taken(email, exclude_id=0):
    return User.query.filter(func.lower(User.email) == email.lower(), User.id != exclude_id).first() is not None


@bp.route("/")
@access("employees.view")
def index():
    text = request.args.get("q", "").strip()
    dept = request.args.get("dept", type=int)
    role = request.args.get("role", type=int)
    status = request.args.get("status", "active")
    can_manage = has_perm(current_user, "employees.manage")
    q = User.query
    if not can_manage:
        status = "active"
    if status == "active":
        q = q.filter(User.active.is_(True))
    elif status == "inactive":
        q = q.filter(User.active.is_(False))
    if text:
        like = f"%{text}%"
        q = q.filter(or_(User.name.ilike(like), User.email.ilike(like), User.job_title.ilike(like),
                         User.employee_code.ilike(like)))
    if dept:
        q = q.filter(User.department_id == dept)
    if role:
        q = q.filter(User.role_id == role)
    pager = paginate(q.order_by(User.name), 20)
    return render_template("employees/index.html", pager=pager, q=text, dept=dept, role=role, status=status,
                           departments=Department.query.order_by(Department.name).all(),
                           roles=Role.query.order_by(Role.level.desc()).all(), can_manage=can_manage, can_add=_can_add(current_user))


@bp.route("/new", methods=["GET", "POST"])
@access()
def new():
    if not _can_add(current_user):
        abort(403)
    full = has_perm(current_user, "employees.manage")
    form = _form(current_user)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        if _email_taken(d["email"]):
            form.fields[1].error = "Another employee already uses this email."
        else:
            role = db.session.get(Role, d["role_id"])
            if role.level >= current_user.role.level and not current_user.is_super:
                abort(403)
            user = User(name=d["name"], email=d["email"], job_title=d["job_title"], phone=d["phone"],
                        department_id=d["department_id"] if full else current_user.department_id, role_id=role.id,
                        joined_on=d["joined_on"] or today(), must_change_password=True,
                        employment_type=d["employment_type"], reports_to_id=d["reports_to_id"])
            password = d["password"] or temp_password()
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            assign_employee_code(user)
            log_activity("created", "user", user.id, f"Created employee {user.name} ({role.name})")
            notify(user, "Welcome to HIMS", "Your account is ready. Please review your profile.", url_for("employees.profile", uid=user.id))
            db.session.commit()
            flash(f"{user.name} was added. Temporary password: {password}  (shown once, share it privately).", "success")
            return redirect(url_for("employees.profile", uid=user.id))
    return render_template("form_page.html", form=form, title="Add employee",
                           subtitle="Create a HIMS account and place the person in a department.",
                           submit="Add employee", cancel=url_for("employees.index"))


@bp.route("/<int:uid>")
@access("employees.view")
def profile(uid):
    from .people import people_actions
    person = User.query.get_or_404(uid)
    manager_view = (current_user.id == person.id or can_manage_user(current_user, person)
                    or current_user.manages_department(person.department_id))
    stats = None
    if manager_view:
        month_start = today().replace(day=1)
        year_start = today().replace(month=1, day=1)
        from ..services import get_setting
        used = dict(db.session.query(LeaveRequest.leave_type, func.sum(LeaveRequest.days))
                    .filter(LeaveRequest.user_id == person.id, LeaveRequest.status == "approved",
                            LeaveRequest.start_date >= year_start).group_by(LeaveRequest.leave_type).all())
        stats = {
            "open_tasks": Task.query.filter(Task.assignee_id == person.id, Task.status.in_(TASK_OPEN)).count(),
            "done_30": Task.query.filter(Task.assignee_id == person.id, Task.status == "completed",
                                         Task.completed_at >= today() - timedelta(days=30)).count(),
            "present": Attendance.query.filter(Attendance.user_id == person.id, Attendance.work_date >= month_start).count(),
            "late": Attendance.query.filter(Attendance.user_id == person.id, Attendance.work_date >= month_start,
                                            Attendance.status == "late").count(),
            "leave": [(name, used.get(key, 0) or 0, get_int(f"leave_{key}", 0))
                      for key, name in (("annual", "Annual"), ("sick", "Sick"), ("casual", "Casual"))],
        }
    recent = Task.query.filter(Task.assignee_id == person.id, Task.status.in_(TASK_OPEN)).order_by(Task.due_date).limit(5).all() if manager_view else []
    can_edit = can_manage_user(current_user, person)
    promo = promotion_options(current_user, person)
    can_docs = current_user.id == person.id or has_perm(current_user, "employees.documents")
    doc_types = DocumentType.query.filter_by(active=True).order_by(DocumentType.category, DocumentType.name).all() if can_docs else []
    have = {d.document_type_id: d for d in EmployeeDocument.query.filter_by(user_id=person.id).all()} if can_docs else {}
    doc_rows = [{"type": t, "doc": have.get(t.id)} for t in doc_types]
    return render_template("employees/profile.html", person=person, stats=stats, recent=recent, can_edit=can_edit,
                           is_self=current_user.id == person.id, direct_reports=[u for u in person.direct_reports if u.active],
                           can_rights=can_edit and person.id != current_user.id and not person.is_super,
                           overrides=len(person.permission_overrides), can_docs=can_docs, doc_rows=doc_rows,
                           missing_required=sum(1 for r in doc_rows if r["type"].required and r["doc"] is None), promo=promo,
                           staffing=people_actions(current_user, person))


@bp.route("/<int:uid>/edit", methods=["GET", "POST"])
@access()
def edit(uid):
    person = User.query.get_or_404(uid)
    full = has_perm(current_user, "employees.manage")
    if not (can_manage_user(current_user, person) or (person.id == current_user.id and full)):
        abort(403)
    form = _form(current_user, person).fill(person)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        manager_field = next(f for f in form.fields if f.name == "reports_to_id")
        if _email_taken(d["email"], person.id):
            form.fields[1].error = "Another employee already uses this email."
        elif d["reports_to_id"] and _creates_cycle(person, d["reports_to_id"]):
            manager_field.error = "That would make the reporting line circular."
        else:
            if not full:
                d["department_id"] = person.department_id          # a head cannot move people between departments
            changes = []
            if "role_id" in d and d["role_id"] != person.role_id:
                role = db.session.get(Role, d["role_id"])
                if role.level >= current_user.role.level and not current_user.is_super:
                    abort(403)
                changes.append(f"role to {role.name}")
                person.role_id = role.id
                notify(person, "Your role was updated", f"You are now {role.name}.", url_for("employees.profile", uid=person.id))
            if d["department_id"] != person.department_id:
                changes.append("department")
            for attr in ("name", "email", "job_title", "phone", "department_id", "joined_on", "employment_type", "reports_to_id"):
                setattr(person, attr, d[attr])
            log_activity("updated", "user", person.id, f"Updated {person.name}" + (f" ({', '.join(changes)})" if changes else ""))
            db.session.commit()
            flash("Employee details saved.", "success")
            return redirect(url_for("employees.profile", uid=person.id))
    return render_template("form_page.html", form=form, title=f"Edit {person.name}", subtitle=person.employee_code or "",
                           submit="Save changes", cancel=url_for("employees.profile", uid=person.id))


@bp.route("/<int:uid>/reset-password", methods=["POST"])
@access()
def reset_password(uid):
    person = User.query.get_or_404(uid)
    if not can_manage_user(current_user, person):
        abort(403)
    password = temp_password()
    person.set_password(password)
    person.must_change_password = True
    person.failed_logins, person.locked_until = 0, None
    log_activity("password_reset", "user", person.id, f"Reset password for {person.name}")
    db.session.commit()
    flash(f"New temporary password for {person.name}: {password}  (shown once). They must change it when they sign in.", "success")
    return redirect(url_for("employees.profile", uid=person.id))


@bp.route("/<int:uid>/toggle", methods=["POST"])
@access()
def toggle(uid):
    person = User.query.get_or_404(uid)
    if not can_manage_user(current_user, person) or person.id == current_user.id:
        abort(403)
    person.active = not person.active
    if person.active:
        person.task_assignable = True          # someone brought back can be given tasks again
    log_activity("deactivated" if not person.active else "reactivated", "user", person.id,
                 f"{'Deactivated' if not person.active else 'Reactivated'} {person.name}")
    db.session.commit()
    flash(f"{person.name} is now {'active' if person.active else 'inactive and can no longer sign in'}.", "success")
    return redirect(url_for("employees.profile", uid=person.id))


# ---------------------------------------------------------------- individual rights (exceptions to the role)
@bp.route("/<int:uid>/rights", methods=["GET", "POST"])
@access()
def rights(uid):
    person = User.query.get_or_404(uid)
    link = url_for("employees.profile", uid=person.id)
    if person.id == current_user.id or not can_manage_user(current_user, person):
        abort(403)
    if person.is_super:
        flash("The Super Admin always has full access, so there is nothing to adjust.", "info")
        return redirect(link)
    perms = Permission.query.order_by(Permission.module, Permission.id).all()
    role_codes = {p.code for p in person.role.permissions}
    existing = {o.permission_id: o for o in person.permission_overrides}

    if request.method == "POST":
        changed = 0
        for p in perms:
            row = existing.get(p.id)
            previous = None if row is None else row.allow
            choice = request.form.get(f"p_{p.id}", "")
            if choice == "allow":
                # you can only hand out a right you hold yourself; otherwise the setting stays as it was
                new = True if has_perm(current_user, p.code) else previous
            elif choice == "deny":
                new = False
            else:
                new = None
            if new is not None and ((new and p.code in role_codes) or (not new and p.code not in role_codes)):
                new = None                    # identical to what the role already gives: no exception needed
            if new == previous:
                continue
            changed += 1
            if new is None:
                person.permission_overrides.remove(row)
            elif row is not None:
                row.allow = new
            else:
                person.permission_overrides.append(UserPermission(permission_id=p.id, allow=new))
        if changed:
            log_activity("rights", "user", person.id, f"Changed individual rights for {person.name} ({changed} change{'s' if changed != 1 else ''})")
            notify(person, "Your access was updated", "An administrator changed some of your individual rights.", url_for("dashboard.index"))
        db.session.commit()
        flash(f"Saved. {changed} change{'s' if changed != 1 else ''}." if changed else "Nothing changed.", "success")
        return redirect(url_for("employees.rights", uid=person.id))

    modules = {}
    for p in perms:
        row = existing.get(p.id)
        state = "default" if row is None else ("allow" if row.allow else "deny")
        effective = (p.code in role_codes) if row is None else row.allow
        modules.setdefault(p.module, []).append({"perm": p, "from_role": p.code in role_codes, "state": state,
                                                 "effective": effective, "can_grant": has_perm(current_user, p.code)})
    return render_template("employees/rights.html", person=person, modules=modules)


# ---------------------------------------------------------------- bulk import from a spreadsheet (CSV)
IMPORT_COLUMNS = ["name", "email", "department", "role", "job_title", "phone", "employment_type", "reports_to_email"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_IMPORT_ROWS = 300


@bp.route("/import/sample.csv")
@access("employees.manage")
def import_sample():
    return csv_response("employee_import_sample.csv", IMPORT_COLUMNS, [
        ["Ali Raza", "ali.raza@homemate.pk", "Sales", "Employee", "Sales Executive", "0300-1234567", "Full-time", "usman.tariq@homemate.local"],
        ["Hina Butt", "hina.butt@homemate.pk", "Marketing", "Intern", "Design Intern", "", "Intern", ""],
    ])


@bp.route("/import", methods=["GET", "POST"])
@access("employees.manage")
def import_csv():
    results, created = [], 0
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file to import.", "error")
        elif not upload.filename.lower().endswith(".csv"):
            flash("The file must be a .csv file. In Excel use File > Save As > CSV.", "error")
        else:
            raw = upload.read(1_000_001)
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
            header = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
            if len(raw) > 1_000_000:
                flash("That file is too large. Split it into smaller files.", "error")
            elif not {"name", "email"} <= header:
                flash("The first row must contain at least the column headings 'name' and 'email'. Download the sample file to see the layout.", "error")
            else:
                depts = {}
                for d in Department.query.all():
                    depts[d.name.lower()], depts[d.slug] = d, d
                roles = {}
                for r in _assignable_roles(current_user):
                    roles[r.name.lower()], roles[r.key] = r, r
                etypes = {}
                for key, text in EMPLOYMENT_TYPES:
                    etypes[key], etypes[text.lower()] = key, key
                seen = set()
                for line, rawrow in enumerate(reader, start=2):
                    if line - 1 > MAX_IMPORT_ROWS:
                        results.append({"line": line, "name": "", "email": "", "ok": False,
                                        "message": f"Stopped: an import is limited to {MAX_IMPORT_ROWS} people at a time. Import the rest in a second file."})
                        break
                    row = {(k or "").strip().lower(): (v or "").strip() for k, v in rawrow.items() if k}
                    name, email = row.get("name", ""), row.get("email", "").lower()
                    if not any(row.values()):
                        continue                                   # blank line
                    problem = None
                    role = roles.get(row.get("role", "").lower()) if row.get("role") else roles.get("employee")
                    dept = depts.get(row.get("department", "").lower()) if row.get("department") else None
                    manager = None
                    if not name:
                        problem = "Name is missing."
                    elif not _EMAIL.match(email):
                        problem = "Email is missing or not valid."
                    elif email in seen or _email_taken(email):
                        problem = "This email is already used by another employee."
                    elif role is None:
                        problem = f"Role '{row.get('role') or 'Employee'}' is not one you can assign."
                    elif row.get("department") and dept is None:
                        problem = f"Department '{row.get('department')}' was not found."
                    elif row.get("employment_type") and row["employment_type"].lower() not in etypes:
                        problem = f"Employment type '{row['employment_type']}' is not recognised."
                    elif row.get("reports_to_email"):
                        manager = User.query.filter(func.lower(User.email) == row["reports_to_email"].lower(), User.active.is_(True)).first()
                        if manager is None:
                            problem = f"Line manager '{row['reports_to_email']}' was not found."
                        elif manager.role.level < 40:
                            problem = f"{manager.name} cannot be a line manager. Managers must be Team Lead level or above."
                    if problem:
                        results.append({"line": line, "name": name, "email": email, "ok": False, "message": problem})
                        continue
                    etype = etypes.get(row.get("employment_type", "").lower()) or ("intern" if role.key == "intern" else "full_time")
                    password = temp_password()
                    user = User(name=name, email=email, job_title=row.get("job_title") or None, phone=row.get("phone") or None,
                                department_id=dept.id if dept else None, role_id=role.id, joined_on=today(), must_change_password=True,
                                employment_type=etype, reports_to_id=manager.id if manager else None)
                    user.set_password(password)
                    db.session.add(user)
                    db.session.flush()
                    assign_employee_code(user)
                    seen.add(email)
                    created += 1
                    results.append({"line": line, "name": name, "email": email, "ok": True, "message": f"{role.name}, {dept.name if dept else 'no department'}",
                                    "password": password})
                if created:
                    log_activity("imported", "user", None, f"Imported {created} employee{'s' if created != 1 else ''} from a CSV file")
                db.session.commit()
                if not results:
                    flash("The file has no people in it.", "error")
    return render_template("employees/import.html", results=results, created=created,
                           failed=sum(1 for r in results if not r["ok"]), columns=IMPORT_COLUMNS, max_rows=MAX_IMPORT_ROWS)


# ---------------------------------------------------------------- employee documents & certifications
def _can_touch_docs(actor, person):
    return actor.id == person.id or has_perm(actor, "employees.documents")


@bp.route("/<int:uid>/documents/<int:type_id>/upload", methods=["POST"])
@access()
def doc_upload(uid, type_id):
    person = User.query.get_or_404(uid)
    if not _can_touch_docs(current_user, person):
        abort(403)
    doc_type = DocumentType.query.get_or_404(type_id)
    link = url_for("employees.profile", uid=person.id) + "#documents"
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a file to upload.", "error")
        return redirect(link)
    try:
        stored, original, _size = save_upload(upload, "employee_documents")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(link)
    expiry = request.form.get("expiry_date") or None
    if doc_type.expiry_required and not expiry:
        flash(f"{doc_type.name} requires an expiry date.", "error")
        return redirect(link)
    existing = EmployeeDocument.query.filter_by(user_id=person.id, document_type_id=doc_type.id).first()
    if existing:
        # replaced, not silently overwritten: the old file is removed only after the new one is safely saved
        from ..services import delete_upload
        delete_upload(existing.file_name)
        db.session.delete(existing)
        db.session.flush()
    record = EmployeeDocument(user_id=person.id, document_type_id=doc_type.id, file_name=stored, original_name=original,
                              expiry_date=expiry, uploaded_by_id=current_user.id)
    db.session.add(record)
    log_activity("uploaded", "employee_document", person.id, f"Uploaded {doc_type.name} for {person.name}")
    db.session.commit()
    flash(f"{doc_type.name} uploaded.", "success")
    return redirect(link)


@bp.route("/<int:uid>/documents/<int:doc_id>/verify", methods=["POST"])
@access("employees.documents")
def doc_verify(uid, doc_id):
    person = User.query.get_or_404(uid)
    doc = EmployeeDocument.query.filter_by(id=doc_id, user_id=person.id).first_or_404()
    doc.verified, doc.verified_by_id, doc.verified_at = True, current_user.id, now()
    log_activity("verified", "employee_document", person.id, f"Verified {doc.document_type.name} for {person.name}")
    db.session.commit()
    flash("Marked as verified.", "success")
    return redirect(url_for("employees.profile", uid=person.id) + "#documents")


@bp.route("/<int:uid>/documents/<int:doc_id>/delete", methods=["POST"])
@access()
def doc_delete(uid, doc_id):
    person = User.query.get_or_404(uid)
    if not _can_touch_docs(current_user, person):
        abort(403)
    doc = EmployeeDocument.query.filter_by(id=doc_id, user_id=person.id).first_or_404()
    from ..services import delete_upload
    delete_upload(doc.file_name)
    log_activity("deleted", "employee_document", person.id, f"Deleted {doc.document_type.name} for {person.name}")
    db.session.delete(doc)
    db.session.commit()
    flash("Document deleted.", "success")
    return redirect(url_for("employees.profile", uid=person.id) + "#documents")


@bp.route("/<int:uid>/documents/<int:doc_id>/file")
@access()
def doc_file(uid, doc_id):
    person = User.query.get_or_404(uid)
    if not _can_touch_docs(current_user, person):
        abort(403)
    doc = EmployeeDocument.query.filter_by(id=doc_id, user_id=person.id).first_or_404()
    try:
        path = upload_path(doc.file_name)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=doc.original_name)


@bp.route("/<int:uid>/id-card")
@access("employees.view")
def id_card(uid):
    person = User.query.get_or_404(uid)
    return render_template("employees/id_card.html", person=person)


# ---------------------------------------------------------------- promotion / demotion (one rung at a time)
@bp.route("/<int:uid>/promote", methods=["POST"])
@access("employees.promote")
def promote(uid):
    person = User.query.get_or_404(uid)
    direction = request.form.get("direction")
    link = url_for("employees.profile", uid=person.id)
    options = promotion_options(current_user, person)
    new_role = options.get(direction) if direction in ("up", "down") else None
    if new_role is None:
        flash("That change isn't available for this person right now.", "error")
        return redirect(link)
    old_role = person.role
    person.role_id = new_role.id
    verb = "Promoted" if direction == "up" else "Moved back"
    log_activity("promoted" if direction == "up" else "demoted", "user", person.id,
                f"{verb} {person.name} from {old_role.name} to {new_role.name}")
    notify(person, f"Your role changed to {new_role.name}",
          f"{current_user.name} {'promoted' if direction == 'up' else 'moved'} you from {old_role.name} to {new_role.name}.",
          url_for("dashboard.index"))
    db.session.commit()
    flash(f"{person.name} is now {new_role.name}.", "success")
    return redirect(link)
