import re
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..constants import DEPT_TOOLS, TASK_OPEN
from ..extensions import db
from ..forms import Field, Form
from ..models import Announcement, Attendance, Department, Document, Task, User, UserDepartmentRole
from ..security import access, can_view_department
from ..services import log_activity
from ..timeutil import today

bp = Blueprint("departments", __name__, url_prefix="/departments")

ICON_CHOICES = [("building", "Building"), ("pen-tool", "Design"), ("trending-up", "Growth"), ("headphones", "Support"),
                ("bar-chart", "Analytics"), ("dollar", "Finance"), ("monitor", "Technology"), ("users", "People"),
                ("flask", "Research"), ("globe", "Online"), ("truck", "Logistics"), ("briefcase", "Business")]


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "department"


@bp.route("/")
@access("departments.view")
def index():
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    members = dict(db.session.query(User.department_id, func.count(User.id)).filter(User.active.is_(True)).group_by(User.department_id).all())
    open_tasks = dict(db.session.query(Task.department_id, func.count(Task.id)).filter(Task.status.in_(TASK_OPEN)).group_by(Task.department_id).all())
    return render_template("departments/index.html", depts=depts, members=members, open_tasks=open_tasks,
                           can_open=lambda d: can_view_department(current_user, d))


def _team_progress(dept, team):
    """Per-person and overall task progress for one department, for the people who lead it.

    Only this department's tasks are counted, so leading a department never exposes another department's workload.
    """
    ids = [u.id for u in team] or [0]
    by_status = {}
    for uid, status, n in (db.session.query(Task.assignee_id, Task.status, func.count(Task.id))
                           .filter(Task.department_id == dept.id, Task.assignee_id.in_(ids))
                           .group_by(Task.assignee_id, Task.status).all()):
        by_status.setdefault(uid, {})[status] = n
    overdue = dict(db.session.query(Task.assignee_id, func.count(Task.id))
                   .filter(Task.department_id == dept.id, Task.assignee_id.in_(ids), Task.status.in_(TASK_OPEN), Task.due_date < today())
                   .group_by(Task.assignee_id).all())

    def row(label_user, counts, late):
        total = sum(n for s, n in counts.items() if s not in ("cancelled", "rejected"))
        done = counts.get("completed", 0)
        return {"user": label_user, "assigned": counts.get("todo", 0) + counts.get("accepted", 0),
                "working": counts.get("in_progress", 0) + counts.get("blocked", 0), "submitted": counts.get("in_review", 0),
                "changes": counts.get("revision", 0), "done": done, "overdue": late, "total": total,
                "rate": round(100 * done / total) if total else None}

    rows = [row(u, by_status.get(u.id, {}), overdue.get(u.id, 0)) for u in team]
    rows = [r for r in rows if r["total"]]
    overall = {}
    for counts in by_status.values():
        for status, n in counts.items():
            overall[status] = overall.get(status, 0) + n
    return {"rows": sorted(rows, key=lambda r: (-(r["rate"] or 0), r["user"].name)), "overall": row(None, overall, sum(overdue.values()))}


@bp.route("/<slug>")
@login_required
def workspace(slug):
    dept = Department.query.filter_by(slug=slug).first_or_404()
    if not can_view_department(current_user, dept):
        abort(403)
    team = [u for u in dept.members if u.active]
    team_role = {}
    for link in UserDepartmentRole.query.filter_by(department_id=dept.id).all():
        if link.user.active and link.user not in team:
            team.append(link.user)
            team_role[link.user.id] = f"{link.role.name} (also works here)"
    team_ids = [u.id for u in team]
    open_q = Task.query.filter(Task.department_id == dept.id, Task.status.in_(TASK_OPEN))
    kpis = {
        "members": len(team),
        "present": Attendance.query.filter(Attendance.work_date == today(), Attendance.user_id.in_(team_ids or [0])).count(),
        "open": open_q.count(),
        "overdue": open_q.filter(Task.due_date < today()).count(),
        "review": Task.query.filter(Task.department_id == dept.id, Task.status == "in_review").count(),
        "done30": Task.query.filter(Task.department_id == dept.id, Task.status == "completed",
                                    Task.completed_at >= today() - timedelta(days=30)).count(),
    }
    tasks = open_q.order_by(Task.due_date.is_(None), Task.due_date).limit(8).all()
    announcements = Announcement.query.filter_by(department_id=dept.id).order_by(Announcement.created_at.desc()).limit(3).all()
    docs = Document.query.filter_by(department_id=dept.id).order_by(Document.created_at.desc()).limit(5).all()
    tools = DEPT_TOOLS.get(dept.slug, [])
    progress = _team_progress(dept, team) if current_user.manages_department(dept.id) else None
    return render_template("departments/workspace.html", dept=dept, team=team, team_role=team_role, kpis=kpis, tasks=tasks,
                           tools=tools, announcements=announcements, docs=docs, progress=progress)


def _form(department=None):
    fields = [
        Field("name", "Department name", required=True),
        Field("icon", "Icon", "select", options=ICON_CHOICES),
        Field("description", "What this department does", "textarea", half=False, rows=3),
        Field("functions", "Sub-functions", half=False, help="Separate with commas, e.g. Recruitment, Onboarding, Attendance & leave."),
    ]
    if department is not None:
        fields.append(Field("active", "Department is active", "checkbox"))
    return Form(fields)


@bp.route("/new", methods=["GET", "POST"])
@access("departments.manage")
def new():
    form = _form()
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        if Department.query.filter(func.lower(Department.name) == d["name"].lower()).first():
            form.fields[0].error = "A department with this name already exists."
        else:
            slug, n = _slugify(d["name"]), 1
            while Department.query.filter_by(slug=slug).first():
                n += 1
                slug = f"{_slugify(d['name'])}-{n}"
            dept = Department(slug=slug, name=d["name"], icon=d["icon"] or "building",
                              description=d["description"], functions=d["functions"])
            db.session.add(dept)
            db.session.flush()
            log_activity("created", "department", dept.id, f"Created department {dept.name}")
            db.session.commit()
            flash("Department created.", "success")
            return redirect(url_for("departments.workspace", slug=dept.slug))
    return render_template("form_page.html", form=form, title="New department", subtitle="Add a team to Home Mate.",
                           submit="Create department", cancel=url_for("departments.index"))


@bp.route("/<slug>/edit", methods=["GET", "POST"])
@access("departments.manage")
def edit(slug):
    dept = Department.query.filter_by(slug=slug).first_or_404()
    form = _form(dept).fill(dept)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        clash = Department.query.filter(func.lower(Department.name) == d["name"].lower(), Department.id != dept.id).first()
        if clash:
            form.fields[0].error = "A department with this name already exists."
        else:
            dept.name, dept.icon = d["name"], d["icon"] or dept.icon
            dept.description, dept.functions, dept.active = d["description"], d["functions"], d["active"]
            log_activity("updated", "department", dept.id, f"Updated department {dept.name}")
            db.session.commit()
            flash("Department saved.", "success")
            return redirect(url_for("departments.workspace", slug=dept.slug))
    return render_template("form_page.html", form=form, title=f"Edit {dept.name}", subtitle="The department key stays the same so access rules keep working.",
                           submit="Save changes", cancel=url_for("departments.workspace", slug=dept.slug))
