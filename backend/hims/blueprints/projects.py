"""Projects: a named body of work with a manager, members and a deadline. Progress is calculated from its tasks."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..constants import PRIORITIES, PROJECT_OPEN, PROJECT_STATUSES, TASK_OPEN
from ..extensions import db
from ..forms import Field, Form
from ..models import Department, Project, ProjectMember, Task, User
from ..scopes import assignable_users
from ..security import access, has_perm, is_pm_user
from ..services import add_comment, comments_for, log_activity, notify, paginate
from ..ui import label

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _can_manage(project):
    return (current_user.is_admin_level or is_pm_user(current_user) or project.manager_id == current_user.id
            or current_user.manages_department(project.department_id))


def _visible():
    if current_user.is_admin_level or is_pm_user(current_user):
        return Project.query
    ids = [current_user.id]
    return (Project.query.outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
            .filter(or_(Project.manager_id == current_user.id, ProjectMember.user_id.in_(ids),
                        Project.department_id == current_user.department_id)).distinct())


def _get(pid):
    p = _visible().filter(Project.id == pid).first_or_404()
    return p


@bp.route("/")
@access("projects.view")
def index():
    status = request.args.get("status", "open")
    text = request.args.get("q", "").strip()
    q = _visible()
    if status == "open":
        q = q.filter(Project.status.in_(PROJECT_OPEN))
    elif status != "all":
        q = q.filter(Project.status == status)
    if text:
        q = q.filter(Project.name.ilike(f"%{text}%"))
    pager = paginate(q.order_by(Project.end_date.is_(None), Project.end_date), 15)
    return render_template("projects/index.html", pager=pager, status=status, q=text, statuses=PROJECT_STATUSES,
                           can_create=has_perm(current_user, "projects.manage"))


@bp.route("/new", methods=["GET", "POST"])
@access("projects.manage")
def new():
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    people = assignable_users(current_user)
    form = Form([
        Field("name", "Project name", required=True, half=False),
        Field("department_id", "Department", "select", coerce=int, options=[(d.id, d.name) for d in depts], blank_label="Cross-department"),
        Field("manager_id", "Project manager", "select", required=True, coerce=int,
              options=[(u.id, u.name) for u in ([current_user] + [p for p in people if p.id != current_user.id])], value=current_user.id),
        Field("start_date", "Start date", "date"), Field("end_date", "Deadline", "date"),
        Field("budget", "Budget", "number", min=0, placeholder="Optional"),
        Field("description", "Description", "textarea", half=False, rows=4),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        project = Project(status="planning", **d)
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectMember(project_id=project.id, user_id=current_user.id))
        if d["manager_id"] != current_user.id:
            db.session.add(ProjectMember(project_id=project.id, user_id=d["manager_id"]))
        log_activity("created", "project", project.id, f'Created project "{project.name}"')
        db.session.commit()
        flash("Project created.", "success")
        return redirect(url_for("projects.detail", pid=project.id))
    return render_template("form_page.html", form=form, title="New project", subtitle="Group related tasks together with one owner and one deadline.",
                           submit="Create project", cancel=url_for("projects.index"))


@bp.route("/<int:pid>")
@access("projects.view")
def detail(pid):
    project = _get(pid)
    by_status = {k: [t for t in project.tasks if t.status == k] for k, _ in [("todo", 0)]}
    open_tasks = [t for t in project.tasks if t.status in TASK_OPEN]
    done_tasks = [t for t in project.tasks if t.status == "completed"]
    can_manage = _can_manage(project)
    candidates = [u for u in assignable_users(current_user) if u.id not in {m.id for m in project.members}] if can_manage else []
    return render_template("projects/detail.html", project=project, open_tasks=open_tasks, done_tasks=done_tasks,
                           comments=comments_for("project", project.id), can_manage=can_manage, candidates=candidates)


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@access("projects.manage")
def edit(pid):
    project = _get(pid)
    if not _can_manage(project):
        abort(403)
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    people = assignable_users(current_user)
    form = Form([
        Field("name", "Project name", required=True, half=False),
        Field("department_id", "Department", "select", coerce=int, options=[(d.id, d.name) for d in depts], blank_label="Cross-department"),
        Field("manager_id", "Project manager", "select", required=True, coerce=int,
              options=[(u.id, u.name) for u in ({project.manager, current_user} | set(people))]),
        Field("status", "Status", "select", required=True, options=PROJECT_STATUSES),
        Field("start_date", "Start date", "date"), Field("end_date", "Deadline", "date"),
        Field("budget", "Budget", "number", min=0),
        Field("description", "Description", "textarea", half=False, rows=4),
    ]).fill(project)
    if request.method == "POST" and form.validate(request.form):
        for key, value in form.data.items():
            setattr(project, key, value)
        log_activity("updated", "project", project.id, f'Updated project "{project.name}"')
        db.session.commit()
        flash("Project saved.", "success")
        return redirect(url_for("projects.detail", pid=project.id))
    return render_template("form_page.html", form=form, title="Edit project", subtitle=project.name,
                           submit="Save changes", cancel=url_for("projects.detail", pid=project.id))


@bp.route("/<int:pid>/members", methods=["POST"])
@access("projects.manage")
def add_member(pid):
    project = _get(pid)
    if not _can_manage(project):
        abort(403)
    person = next((u for u in assignable_users(current_user) if u.id == request.form.get("user_id", type=int)), None)
    if person is None or person.id in {m.id for m in project.members}:
        flash("Choose someone to add who isn't already on the project.", "error")
    else:
        db.session.add(ProjectMember(project_id=project.id, user_id=person.id))
        notify(person, "Added to a project", f'You were added to "{project.name}".', url_for("projects.detail", pid=project.id), kind="request")
        log_activity("updated", "project", project.id, f"Added {person.name} to {project.name}")
        db.session.commit()
        flash(f"{person.name} added to the project.", "success")
    return redirect(url_for("projects.detail", pid=project.id))


@bp.route("/<int:pid>/members/<int:uid>/remove", methods=["POST"])
@access("projects.manage")
def remove_member(pid, uid):
    project = _get(pid)
    if not _can_manage(project):
        abort(403)
    row = ProjectMember.query.filter_by(project_id=project.id, user_id=uid).first()
    if row and row.user_id != project.manager_id:
        db.session.delete(row)
        log_activity("updated", "project", project.id, f"Removed a member from {project.name}")
        db.session.commit()
        flash("Removed from the project.", "success")
    return redirect(url_for("projects.detail", pid=project.id))


@bp.route("/<int:pid>/comment", methods=["POST"])
@access("projects.view")
def comment(pid):
    project = _get(pid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("project", project.id, body)
        db.session.commit()
    return redirect(url_for("projects.detail", pid=project.id) + "#comments")
