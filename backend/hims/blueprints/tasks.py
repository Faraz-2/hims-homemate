from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from .. import workflow
from ..constants import PRIORITIES, TASK_FLOW, TASK_OPEN, TASK_STATUSES
from ..extensions import db
from ..forms import Field, Form
from ..models import Campaign, Comment, Department, Project, Task, TaskChecklistItem, User
from ..scopes import assignable_users, task_department_for, visible_tasks
from ..security import access, dept_ok, has_perm
from ..services import (add_comment, comments_for, log_activity, next_submission_version, notify, paginate,
                        save_upload, upload_path)
from ..timeutil import now, today
from ..ui import label

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

# Board columns group related statuses so nothing silently disappears from the kanban view.
# "rejected" and "cancelled" behave the same way on the board: they stop appearing here (they're
# still visible through the status filter in the list view), because a board of closed-out cards
# isn't useful day to day.
BOARD_COLUMNS = [
    ("todo", "Assigned", ("todo", "accepted")),
    ("in_progress", None, ("in_progress", "blocked")),
    ("in_review", None, ("in_review",)),
    ("revision", None, ("revision",)),
    ("completed", None, ("completed",)),
]


def _assignable(user):
    return assignable_users(user)          # rules live in scopes.py so they can be explained on the Rights page


def _form(user, task=None):
    people = _assignable(user)
    fields = [
        Field("title", "Task title", required=True, half=False),
        Field("description", "Details", "textarea", half=False, rows=5),
    ]
    if len(people) > 1:
        fields.append(Field("assignee_id", "Assign to", "select", required=True, coerce=int,
                            options=[(p.id, f"{p.name} ({p.department.name if p.department else 'No department'})") for p in people]))
    open_projects = Project.query.filter(Project.status.in_(("planning", "active", "on_hold"))).all()
    projects = (open_projects if user.is_admin_level else
               [p for p in open_projects if p.manager_id == user.id or user.id in {m.id for m in p.members}
                or p.department_id == user.department_id])
    projects.sort(key=lambda p: p.name)
    if projects:
        fields.append(Field("project_id", "Project (optional)", "select", coerce=int, blank_label="No project",
                            options=[(p.id, p.name) for p in projects]))
    if user.is_admin_level or dept_ok(user, "campaigns"):
        open_campaigns = Campaign.query.filter(Campaign.status.in_(("planning", "active", "paused"))).order_by(Campaign.name).all()
        campaigns = open_campaigns if user.is_admin_level else [c for c in open_campaigns if c.department_id == user.department_id or c.owner_id == user.id]
        if campaigns:
            fields.append(Field("campaign_id", "Campaign (optional)", "select", coerce=int, blank_label="No campaign",
                                options=[(c.id, c.name) for c in campaigns]))
    fields += [
        Field("priority", "Priority", "select", required=True, options=PRIORITIES, value="medium"),
        Field("due_date", "Due date", "date"),
    ]
    return Form(fields), people


def _can_edit(task):
    return current_user.id == task.created_by_id or current_user.manages_department(task.department_id)


def _get_visible(tid):
    return visible_tasks(current_user).filter(Task.id == tid).first_or_404()


@bp.route("/")
@access("tasks.view")
def index():
    view = request.args.get("view", "list")
    scope = request.args.get("scope") or ("mine" if not current_user.is_manager_level else "all")
    text = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    dept = request.args.get("dept", type=int)
    assignee = request.args.get("assignee", type=int)

    q = visible_tasks(current_user)
    if scope == "mine":
        q = q.filter(Task.assignee_id == current_user.id)
    elif scope == "created":
        q = q.filter(Task.created_by_id == current_user.id)
    if text:
        q = q.filter(or_(Task.title.ilike(f"%{text}%"), Task.description.ilike(f"%{text}%")))
    if status == "open":
        q = q.filter(Task.status.in_(TASK_OPEN))
    elif status == "overdue":
        q = q.filter(Task.status.in_(TASK_OPEN), Task.due_date < today())
    elif status:
        q = q.filter(Task.status == status)
    if priority:
        q = q.filter(Task.priority == priority)
    if dept and current_user.is_admin_level:
        q = q.filter(Task.department_id == dept)
    if assignee:
        q = q.filter(Task.assignee_id == assignee)

    ctx = dict(view=view, scope=scope, q=text, status=status, priority=priority, dept=dept, assignee=assignee,
               departments=Department.query.order_by(Department.name).all(),
               people=_assignable(current_user) if current_user.is_manager_level else [])
    if view == "board":
        columns = []
        for key, col_label, statuses in BOARD_COLUMNS:
            col = q.filter(Task.status.in_(statuses))
            col = col.order_by(Task.updated_at.desc()) if key == "completed" else col.order_by(Task.due_date.is_(None), Task.due_date)
            columns.append({"key": key, "label": col_label or label(key), "count": col.count(), "tasks": col.limit(30).all()})
        return render_template("tasks/index.html", columns=columns, pager=None, **ctx)
    order = (Task.status.in_(("completed", "cancelled")), Task.due_date.is_(None), Task.due_date, Task.id.desc())
    pager = paginate(q.order_by(*order), 20)
    return render_template("tasks/index.html", pager=pager, columns=None, **ctx)


@bp.route("/new", methods=["GET", "POST"])
@access("tasks.create")
def new():
    form, people = _form(current_user)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        assignee = current_user
        if "assignee_id" in d:
            assignee = next((p for p in people if p.id == d["assignee_id"]), None)
            if assignee is None:
                abort(403)
        task = Task(title=d["title"], description=d["description"], priority=d["priority"], due_date=d["due_date"],
                    created_by_id=current_user.id, assignee_id=assignee.id, status="todo", project_id=d.get("project_id"),
                    campaign_id=d.get("campaign_id"), department_id=task_department_for(current_user, assignee))
        db.session.add(task)
        db.session.flush()
        add_comment("task", task.id, f"Task created and assigned to {assignee.name}.", kind="status")
        log_activity("created", "task", task.id, f'Created task "{task.title}" for {assignee.name}')
        notify(assignee, "New task assigned", f'"{task.title}" was assigned to you by {current_user.name}.',
               url_for("tasks.detail", tid=task.id), kind="task")
        db.session.commit()
        flash("Task created.", "success")
        return redirect(url_for("tasks.detail", tid=task.id))
    return render_template("form_page.html", form=form, title="New task",
                           subtitle="Create a task and assign it to someone." if len(people) > 1 else "This task will be assigned to you.",
                           submit="Create task", cancel=url_for("tasks.index"))


@bp.route("/<int:tid>")
@access("tasks.view")
def detail(tid):
    task = _get_visible(tid)
    steps = workflow.available(TASK_FLOW, task, current_user, "tasks.approve")
    can_work = current_user.id in (task.assignee_id, task.created_by_id) or current_user.manages_department(task.department_id)
    return render_template("tasks/detail.html", task=task, steps=steps, comments=comments_for("task", task.id),
                           can_edit=_can_edit(task), can_work=can_work,
                           can_delete=has_perm(current_user, "tasks.delete") and current_user.manages_department(task.department_id))


@bp.route("/<int:tid>/edit", methods=["GET", "POST"])
@access("tasks.view")
def edit(tid):
    task = _get_visible(tid)
    if not _can_edit(task):
        abort(403)
    form, people = _form(current_user, task)
    form.fill(task)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        old_assignee = task.assignee_id
        task.title, task.description, task.priority, task.due_date = d["title"], d["description"], d["priority"], d["due_date"]
        if "project_id" in d:
            task.project_id = d["project_id"]
        if "campaign_id" in d:
            task.campaign_id = d["campaign_id"]
        if "assignee_id" in d and d["assignee_id"] != task.assignee_id:
            new_person = next((p for p in people if p.id == d["assignee_id"]), None)
            if new_person is None:
                abort(403)
            task.assignee_id = new_person.id
            task.department_id = task_department_for(current_user, new_person) or task.department_id
            add_comment("task", task.id, f"Reassigned to {new_person.name}.", kind="status")
            notify(new_person, "Task assigned to you", f'"{task.title}" was reassigned to you.',
                   url_for("tasks.detail", tid=task.id), kind="task")
        log_activity("updated", "task", task.id, f'Edited task "{task.title}"' + (" (reassigned)" if old_assignee != task.assignee_id else ""))
        db.session.commit()
        flash("Task saved.", "success")
        return redirect(url_for("tasks.detail", tid=task.id))
    return render_template("form_page.html", form=form, title="Edit task", subtitle=task.title,
                           submit="Save changes", cancel=url_for("tasks.detail", tid=task.id))


@bp.route("/<int:tid>/transition", methods=["POST"])
@access("tasks.view")
def transition(tid):
    task = _get_visible(tid)
    step = workflow.find(TASK_FLOW, task, request.form.get("to", ""), current_user, "tasks.approve")
    if step is None:
        abort(403)
    note = request.form.get("note", "").strip()
    if step.note and not note:
        flash(f"Add a short note explaining why before you choose “{step.label}”.", "error")
        return redirect(url_for("tasks.detail", tid=task.id))
    task.status = step.dst
    task.completed_at = now() if step.dst == "completed" else None
    add_comment("task", task.id, f"{step.label}: status changed to {label(step.dst)}." + (f"\n{note}" if note else ""), kind="status")
    log_activity("status", "task", task.id, f'"{task.title}" → {label(step.dst)}')
    link = url_for("tasks.detail", tid=task.id)
    if step.dst == "in_review":
        notify(task.created_by, "Task ready for review", f'{current_user.name} submitted "{task.title}" for review.', link, kind="approval")
    elif step.dst == "revision":
        notify(task.assignee, "Revision requested", f'"{task.title}": {note}', link, kind="revision")
    elif step.dst == "completed":
        notify([task.assignee, task.created_by], "Task approved", f'"{task.title}" was approved and completed.', link, kind="task")
    else:
        notify([task.assignee, task.created_by], f"Task {label(step.dst).lower()}", f'"{task.title}" is now {label(step.dst).lower()}.', link, kind="task")
    db.session.commit()
    flash(f"Task moved to {label(step.dst)}.", "success")
    return redirect(url_for("tasks.detail", tid=task.id))


@bp.route("/<int:tid>/comment", methods=["POST"])
@access("tasks.view")
def comment(tid):
    task = _get_visible(tid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("task", task.id, body)
        notify([task.assignee, task.created_by], "New comment on a task", f'{current_user.name} on "{task.title}": {body[:120]}',
                url_for("tasks.detail", tid=task.id), kind="comment")
        db.session.commit()
    return redirect(url_for("tasks.detail", tid=task.id) + "#comments")


@bp.route("/<int:tid>/checklist", methods=["POST"])
@access("tasks.view")
def checklist_add(tid):
    task = _get_visible(tid)
    if current_user.id not in (task.assignee_id, task.created_by_id) and not current_user.manages_department(task.department_id):
        abort(403)
    text = request.form.get("text", "").strip()[:300]
    if text:
        position = (max((i.position for i in task.checklist), default=0)) + 1
        db.session.add(TaskChecklistItem(task_id=task.id, text=text, position=position))
        db.session.commit()
    return redirect(url_for("tasks.detail", tid=task.id) + "#checklist")


@bp.route("/<int:tid>/checklist/<int:iid>/toggle", methods=["POST"])
@access("tasks.view")
def checklist_toggle(tid, iid):
    task = _get_visible(tid)
    item = next((i for i in task.checklist if i.id == iid), None)
    if item is None:
        abort(404)
    if current_user.id not in (task.assignee_id, task.created_by_id) and not current_user.manages_department(task.department_id):
        abort(403)
    item.done = not item.done
    db.session.commit()
    return redirect(url_for("tasks.detail", tid=task.id) + "#checklist")


@bp.route("/<int:tid>/checklist/<int:iid>/delete", methods=["POST"])
@access("tasks.view")
def checklist_delete(tid, iid):
    task = _get_visible(tid)
    item = next((i for i in task.checklist if i.id == iid), None)
    if item is None:
        abort(404)
    if current_user.id not in (task.assignee_id, task.created_by_id) and not current_user.manages_department(task.department_id):
        abort(403)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("tasks.detail", tid=task.id) + "#checklist")


@bp.route("/<int:tid>/submit", methods=["POST"])
@access("tasks.view")
def submit_work(tid):
    """Attach a file to the task (the assignee's finished work, or a brief from whoever created it).

    Every upload is kept: nothing is overwritten, so the full version history stays on the task.
    """
    task = _get_visible(tid)
    if current_user.id not in (task.assignee_id, task.created_by_id) and not current_user.manages_department(task.department_id):
        abort(403)
    upload = request.files.get("file")
    note = request.form.get("note", "").strip()
    link = url_for("tasks.detail", tid=task.id) + "#comments"
    if not upload or not upload.filename:
        flash("Choose a file to attach.", "error")
        return redirect(link)
    try:
        stored, original, _size = save_upload(upload, "tasks")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(link)
    version = next_submission_version("task", task.id)
    label_word = "Submission" if current_user.id == task.assignee_id else "File"
    db.session.add(Comment(entity_type="task", entity_id=task.id, user_id=current_user.id, kind="submission",
                           body=note or f"{label_word} v{version}: {original}", attachment_file=stored,
                           attachment_name=original, version=version))
    log_activity("uploaded", "task", task.id, f'{current_user.name} attached "{original}" (v{version}) to "{task.title}"')
    notify([task.assignee, task.created_by], f"New file on a task", f'{current_user.name} attached "{original}" to "{task.title}".', link, kind="task")
    db.session.commit()
    flash(f"File attached as version {version}.", "success")
    return redirect(link)


@bp.route("/submission/<int:cid>/file")
@access("tasks.view")
def submission_file(cid):
    c = db.session.get(Comment, cid)
    if c is None or c.entity_type != "task" or not c.attachment_file:
        abort(404)
    _get_visible(c.entity_id)          # 404s if this task isn't visible to the current user
    try:
        path = upload_path(c.attachment_file)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=c.attachment_name or path.name)


@bp.route("/<int:tid>/delete", methods=["POST"])
@access("tasks.delete")
def delete(tid):
    task = _get_visible(tid)
    if not current_user.manages_department(task.department_id):
        abort(403)
    from ..models import Comment
    Comment.query.filter_by(entity_type="task", entity_id=task.id).delete()
    log_activity("deleted", "task", task.id, f'Deleted task "{task.title}"')
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("tasks.index"))
