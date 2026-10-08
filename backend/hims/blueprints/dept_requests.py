"""Requests between departments: Sales to Finance, Marketing to Design, Operations to Vendor Management, ...

Flow: submitted > assigned > in progress > in review > completed (or revision / declined / cancelled).
The receiving department's head assigns it (or a team member picks it up), the requester approves the result.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from .. import workflow
from ..constants import PRIORITIES, REQUEST_CATEGORIES, REQUEST_FLOW, REQUEST_OPEN, REQUEST_STATUSES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Department, InternalRequest, User
from ..scopes import visible_requests
from ..security import access
from ..services import add_comment, comments_for, log_activity, notify, paginate, save_upload, upload_limit_text, upload_path
from ..timeutil import now
from ..ui import label

bp = Blueprint("dept_requests", __name__, url_prefix="/requests")


def _get(rid):
    return visible_requests(current_user).filter(InternalRequest.id == rid).first_or_404()


def _team(dept_id):
    return User.query.filter_by(active=True, department_id=dept_id).order_by(User.name).all()


def _receivers(dept_id):
    """Who gets told about a new incoming request: the department's heads/leads, or everyone if it has none."""
    members = _team(dept_id)
    return [u for u in members if u.is_manager_level] or members


@bp.route("/")
@access("requests.create")
def index():
    my_dept = current_user.department_id
    box = request.args.get("box") or ("inbox" if my_dept else "sent")
    status = request.args.get("status", "open")
    dept = request.args.get("dept", type=int)
    text = request.args.get("q", "").strip()
    q = visible_requests(current_user)
    if box == "inbox":
        q = q.filter(InternalRequest.to_department_id == my_dept) if my_dept else q.filter(InternalRequest.id < 0)
    elif box == "sent":
        q = q.filter(InternalRequest.requester_id == current_user.id)
    elif box == "assigned":
        q = q.filter(InternalRequest.assignee_id == current_user.id)
    if status == "open":
        q = q.filter(InternalRequest.status.in_(REQUEST_OPEN))
    elif status != "all":
        q = q.filter(InternalRequest.status == status)
    if dept:
        q = q.filter(InternalRequest.to_department_id == dept)
    if text:
        like = f"%{text}%"
        q = q.filter(or_(InternalRequest.title.ilike(like), InternalRequest.description.ilike(like)))
    pager = paginate(q.order_by(InternalRequest.due_date.is_(None), InternalRequest.due_date, InternalRequest.id.desc()), 20)

    base = visible_requests(current_user)
    counts = {
        "incoming": base.filter(InternalRequest.to_department_id == my_dept, InternalRequest.status.in_(REQUEST_OPEN)).count() if my_dept else 0,
        "unassigned": base.filter(InternalRequest.to_department_id == my_dept, InternalRequest.status == "submitted",
                                  InternalRequest.assignee_id.is_(None)).count() if my_dept else 0,
        "review": base.filter(InternalRequest.requester_id == current_user.id, InternalRequest.status == "in_review").count(),
        "sent": base.filter(InternalRequest.requester_id == current_user.id, InternalRequest.status.in_(REQUEST_OPEN)).count(),
    }
    return render_template("dept_requests/index.html", pager=pager, box=box, status=status, dept=dept, q=text,
                           statuses=REQUEST_STATUSES, counts=counts, has_dept=bool(my_dept),
                           departments=Department.query.filter_by(active=True).order_by(Department.name).all())


@bp.route("/new", methods=["GET", "POST"])
@access("requests.create")
def new():
    depts = [d for d in Department.query.filter_by(active=True).order_by(Department.name).all() if d.id != current_user.department_id]
    form = Form([
        Field("to_department_id", "Send to department", "select", required=True, coerce=int, options=[(d.id, d.name) for d in depts],
              value=request.args.get("to", "")),
        Field("category", "Type of request", "select", required=True, options=strings(REQUEST_CATEGORIES)),
        Field("title", "What do you need?", required=True, half=False, placeholder="e.g. Payment approval for the September vendor invoices"),
        Field("priority", "Priority", "select", required=True, options=PRIORITIES, value="medium"),
        Field("due_date", "Needed by", "date"),
        Field("description", "Details", "textarea", half=False, rows=6, help="Give enough detail that the other team can start without asking you."),
        Field("attachment", "Attachment (optional)", "file", half=False, help=f"PDF, image, video or Office file, up to {upload_limit_text()}."),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        stored = original = None
        upload = request.files.get("attachment")
        try:
            if upload and upload.filename:
                stored, original, _ = save_upload(upload, "requests")
        except ValueError as exc:
            form.fields[-1].error = str(exc)
        else:
            req = InternalRequest(title=d["title"], category=d["category"], description=d["description"], priority=d["priority"],
                                  due_date=d["due_date"], to_department_id=d["to_department_id"], requester_id=current_user.id,
                                  from_department_id=current_user.department_id, status="submitted",
                                  attachment_file=stored, attachment_name=original)
            db.session.add(req)
            db.session.flush()
            add_comment("request", req.id, f"Request sent to {req.to_department.name} by {current_user.name}.", kind="status")
            log_activity("created", "request", req.id, f"{req.number}: {req.title} ({current_user.department.name if current_user.department else 'No department'} to {req.to_department.name})")
            notify(_receivers(req.to_department_id), f"New request for {req.to_department.name}",
                   f"{current_user.name}: {req.title}", url_for("dept_requests.detail", rid=req.id), kind="request")
            db.session.commit()
            flash(f"{req.number} was sent to {req.to_department.name}.", "success")
            return redirect(url_for("dept_requests.detail", rid=req.id))
    return render_template("form_page.html", form=form, title="New request to another department",
                           subtitle="The receiving team is notified, assigns someone, and you approve the result.",
                           submit="Send request", cancel=url_for("dept_requests.index"))


@bp.route("/<int:rid>")
@access("requests.create")
def detail(rid):
    req = _get(rid)
    roles = workflow.actor_roles(req, current_user, "requests.manage")
    steps = workflow.available(REQUEST_FLOW, req, current_user, "requests.manage")
    in_target = current_user.department_id == req.to_department_id
    open_ = req.status in REQUEST_OPEN
    return render_template("dept_requests/detail.html", req=req, steps=steps, comments=comments_for("request", req.id),
                           can_assign="manager" in roles and open_, can_claim=in_target and open_ and req.assignee_id is None and req.status == "submitted",
                           team=_team(req.to_department_id))


@bp.route("/<int:rid>/transition", methods=["POST"])
@access("requests.create")
def transition(rid):
    req = _get(rid)
    step = workflow.find(REQUEST_FLOW, req, request.form.get("to", ""), current_user, "requests.manage")
    if step is None:
        abort(403)
    link = url_for("dept_requests.detail", rid=req.id)
    note = request.form.get("note", "").strip()
    if step.note and not note:
        flash(f"Add a short note before you choose “{step.label}”.", "error")
        return redirect(link)
    req.status = step.dst
    req.completed_at = now() if step.dst == "completed" else None
    add_comment("request", req.id, f"{step.label}: status changed to {label(step.dst)}." + (f"\n{note}" if note else ""), kind="status")
    log_activity("status", "request", req.id, f"{req.number} → {label(step.dst)}")
    body = f'{req.number} "{req.title}"' + (f": {note}" if note else "")
    if step.dst in ("in_progress", "in_review", "completed", "declined"):
        notify(req.requester, f"Request {label(step.dst).lower()}", body, link, kind="request")
    if step.dst in ("revision", "cancelled", "completed"):
        notify(req.assignee, f"Request {label(step.dst).lower()}", body, link, kind="request")
    db.session.commit()
    flash(f"Request is now {label(step.dst).lower()}.", "success")
    return redirect(link)


@bp.route("/<int:rid>/assign", methods=["POST"])
@access("requests.create")
def assign(rid):
    req = _get(rid)
    if "manager" not in workflow.actor_roles(req, current_user, "requests.manage") or req.status not in REQUEST_OPEN:
        abort(403)
    person = next((u for u in _team(req.to_department_id) if u.id == request.form.get("assignee_id", type=int)), None)
    link = url_for("dept_requests.detail", rid=req.id)
    if person is None:
        flash(f"Choose someone from {req.to_department.name}.", "error")
        return redirect(link)
    req.assignee_id = person.id
    if req.status == "submitted":
        req.status = "assigned"
    add_comment("request", req.id, f"Assigned to {person.name}.", kind="status")
    log_activity("assigned", "request", req.id, f"{req.number} assigned to {person.name}")
    notify([person, req.requester], "Request assigned", f'{req.number} "{req.title}" is assigned to {person.name}.', link, kind="request")
    db.session.commit()
    flash(f"Assigned to {person.name}.", "success")
    return redirect(link)


@bp.route("/<int:rid>/claim", methods=["POST"])
@access("requests.create")
def claim(rid):
    req = _get(rid)
    link = url_for("dept_requests.detail", rid=req.id)
    if current_user.department_id != req.to_department_id:
        abort(403)
    if req.assignee_id is not None or req.status != "submitted":
        flash("Someone already picked this up.", "info")
        return redirect(link)
    req.assignee_id, req.status = current_user.id, "assigned"
    add_comment("request", req.id, f"{current_user.name} picked up this request.", kind="status")
    notify(req.requester, "Your request was picked up", f'{current_user.name} is on {req.number} "{req.title}".', link, kind="request")
    db.session.commit()
    flash("This request is now yours.", "success")
    return redirect(link)


@bp.route("/<int:rid>/comment", methods=["POST"])
@access("requests.create")
def comment(rid):
    req = _get(rid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("request", req.id, body)
        notify([req.requester, req.assignee], "New comment on a request", f'{current_user.name} on {req.number}: {body[:120]}',
               url_for("dept_requests.detail", rid=req.id), kind="comment")
        db.session.commit()
    return redirect(url_for("dept_requests.detail", rid=req.id) + "#comments")


@bp.route("/<int:rid>/file")
@access("requests.create")
def file(rid):
    req = _get(rid)
    if not req.attachment_file:
        abort(404)
    try:
        path = upload_path(req.attachment_file)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=req.attachment_name or path.name)
