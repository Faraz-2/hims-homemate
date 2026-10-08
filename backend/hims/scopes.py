"""Row-level visibility: who may see which records ("protected department data")."""
from sqlalchemy import and_, or_

from .constants import GATES
from .models import (ContentRequest, Department, Document, Expense, LeaveRequest, Role, StoredFile, Task, Ticket, User,
                     UserDepartmentRole)
from .security import has_perm


def visible_tasks(user):
    q = Task.query
    if user.is_admin_level:
        return q
    conds = [Task.assignee_id == user.id, Task.created_by_id == user.id]
    led = user.led_department_ids
    if led:
        conds.append(Task.department_id.in_(led))
    return q.filter(or_(*conds))


def visible_content_requests(user):
    q = ContentRequest.query
    if user.is_admin_level or user.dept_slug in GATES["creative"]:
        return q
    return q.filter(or_(ContentRequest.requested_by_id == user.id, ContentRequest.assignee_id == user.id))


def is_ticket_staff(user, t) -> bool:
    if user.is_admin_level or t.assignee_id == user.id:
        return True
    if t.kind == "csr":
        return user.dept_slug in GATES["csr"]
    if t.kind == "it":
        return user.dept_slug in GATES["it"]
    return t.target_user_id == user.id or (
        t.target_department_id is not None and t.target_department_id == user.department_id)


def visible_tickets(user, kind=None):
    q = Ticket.query
    if kind:
        q = q.filter(Ticket.kind == kind)
    if user.is_admin_level:
        return q
    conds = [Ticket.requester_id == user.id, Ticket.assignee_id == user.id, Ticket.target_user_id == user.id]
    if user.dept_slug in GATES["csr"]:
        conds.append(Ticket.kind == "csr")
    if user.dept_slug in GATES["it"]:
        conds.append(Ticket.kind == "it")
    if user.department_id:
        conds.append(and_(Ticket.kind == "query", Ticket.target_department_id == user.department_id))
    return q.filter(or_(*conds))


def visible_expenses(user):
    q = Expense.query
    if user.is_admin_level or user.dept_slug in GATES["finance"]:
        return q
    conds = [Expense.submitted_by_id == user.id]
    if user.role.level >= 60 and user.department_id:
        conds.append(Expense.department_id == user.department_id)
    return q.filter(or_(*conds))


def visible_documents(user):
    q = Document.query
    if user.is_admin_level:
        return q
    conds = [Document.department_id.is_(None), Document.uploaded_by_id == user.id]
    if user.department_id:
        conds.append(Document.department_id == user.department_id)
    return q.filter(or_(*conds))


def leaves_to_decide(user):
    """Leave requests this user is allowed to approve or reject (never their own)."""
    nothing = LeaveRequest.query.filter(LeaveRequest.id < 0)
    if not has_perm(user, "leaves.approve"):
        return nothing
    q = (LeaveRequest.query
         .join(User, LeaveRequest.user_id == User.id)
         .join(Role, User.role_id == Role.id)
         .filter(User.id != user.id))
    if user.is_super:
        return q
    if user.is_admin_level or (user.dept_slug == "hr" and user.is_manager_level):
        return q.filter(Role.level < 80)
    if user.department_id:
        return q.filter(User.department_id == user.department_id, Role.level < user.role.level)
    return nothing


def can_decide_leave(user, leave) -> bool:
    if leave.user_id == user.id or not has_perm(user, "leaves.approve"):
        return False
    if user.is_super:
        return True
    if user.is_admin_level or (user.dept_slug == "hr" and user.is_manager_level):
        return leave.user.role.level < 80
    return bool(user.department_id and leave.user.department_id == user.department_id
                and user.role.level > leave.user.role.level)


def can_manage_user(actor, target) -> bool:
    """May this person edit, reset the password of, or deactivate the target?

    Admins (employees.manage) manage anyone below their own level. Department heads
    (employees.manage_team) manage people in their own department who rank below them.
    """
    if has_perm(actor, "employees.manage"):
        return actor.is_super or target.role.level < actor.role.level
    if has_perm(actor, "employees.manage_team"):
        return bool(actor.department_id) and target.department_id == actor.department_id and target.role.level < actor.role.level
    return False


def promotion_options(actor, target):
    """Can `actor` move `target` one rung up or down the standard career ladder right now?

    Returns {"up": Role|None, "down": Role|None}. Needs the employees.promote right (Department Head tier
    and above, by default). Admins and Super Admins can move anyone on the ladder; a Department Head can move
    people in their own department, all the way up to Department Head. Nobody can promote someone above their
    own level (this only matters if the right was individually granted to someone lower down).
    """
    from .constants import PROMOTION_LADDER
    from .models import Role
    none = {"up": None, "down": None}
    if not has_perm(actor, "employees.promote") or target.role.key not in PROMOTION_LADDER:
        return none
    if not (actor.is_admin_level or (actor.department_id and actor.department_id == target.department_id)):
        return none
    idx = PROMOTION_LADDER.index(target.role.key)
    up = Role.query.filter_by(key=PROMOTION_LADDER[idx + 1]).first() if idx + 1 < len(PROMOTION_LADDER) else None
    down = Role.query.filter_by(key=PROMOTION_LADDER[idx - 1]).first() if idx > 0 else None
    if up and not actor.is_admin_level and up.level > actor.role.level:
        up = None          # never promote someone above your own level
    return {"up": up, "down": down}


def assignable_users(actor):
    """Who may this person give tasks to?

    * Admin-level: anyone active.
    * Someone holding 'Assign tasks to others': people who report to them directly, plus people in their
      own department who rank below them (including people whose role in that department is a secondary one,
      for example a Co-Head of Sales who is based in Operations). Never a peer, a superior, or another department.
    * Everyone else (employees, interns): only themselves.

    Anyone whose task assignment has been paused (profile > "Pause task assignment") or who has been removed from
    the team is left out of every list; they can still create tasks for themselves if their role allows it.
    """
    from .security import is_pm_user
    q = User.query.filter_by(active=True, task_assignable=True)
    if actor.is_admin_level or (is_pm_user(actor) and has_perm(actor, "projects.manage")):
        people = {u.id: u for u in q.order_by(User.name).all()}
        people.setdefault(actor.id, actor)
        return sorted(people.values(), key=lambda u: u.name)
    people = {actor.id: actor}
    if has_perm(actor, "tasks.assign"):
        led = actor.led_department_ids
        secondary_in_led = {r.user_id for r in UserDepartmentRole.query.filter(UserDepartmentRole.department_id.in_(led or [0])).all()
                            if r.role.level < actor.role.level}
        for u in q.order_by(User.name).all():
            junior_in_dept = u.department_id in led and u.role.level < actor.role.level
            if junior_in_dept or u.reports_to_id == actor.id or u.id in secondary_in_led:
                people[u.id] = u
    return sorted(people.values(), key=lambda u: u.name)


def task_department_for(actor, assignee):
    """Which department a task belongs to. Normally the assignee's own department; but if the assignee only works in
    a department the actor leads as a secondary role (and not in the actor's own), file it under that one so the
    person who gave the task, and that department's leaders, can see it."""
    if assignee.department_id is None:
        return actor.department_id
    if actor.is_admin_level:
        return assignee.department_id
    led = actor.led_department_ids
    if assignee.department_id in led or assignee.id == actor.id or not led:
        return assignee.department_id
    for link in assignee.secondary_roles:
        if link.department_id in led:
            return link.department_id
    return assignee.department_id


def visible_departments(user):
    return Department.query.filter_by(active=True).order_by(Department.name)


def visible_requests(user):
    """Department-to-department requests this person may open.

    Everyone sees requests they sent or are working on. Members of the receiving department see the
    incoming queue (so they can pick work up); department heads also see what their own team sends out.
    Admins see all.
    """
    from sqlalchemy import or_
    from .models import InternalRequest as R
    if user.is_admin_level:
        return R.query
    conds = [R.requester_id == user.id, R.assignee_id == user.id]
    if user.department_id:
        conds.append(R.to_department_id == user.department_id)
        if user.is_manager_level:
            conds.append(R.from_department_id == user.department_id)
    return R.query.filter(or_(*conds))


def user_department_ids(user):
    """The user's main department plus every department they hold a secondary role in."""
    ids = {r.department_id for r in user.secondary_roles}
    if user.department_id:
        ids.add(user.department_id)
    return ids


def visible_files(user):
    """Files in the Files area this person may open: their own, their departments' shared files, company-wide files.
    Administrators see everything (so nothing is ever out of reach of a backup or an investigation)."""
    q = StoredFile.query
    if user.is_admin_level:
        return q
    conds = [StoredFile.owner_id == user.id, StoredFile.visibility == "company"]
    depts = user_department_ids(user)
    if depts:
        conds.append(and_(StoredFile.visibility == "department", StoredFile.department_id.in_(depts)))
    return q.filter(or_(*conds))
