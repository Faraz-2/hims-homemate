"""Design & content requests: any team briefs the Marketing content and creation team."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from .. import workflow
from ..constants import CONTENT_FLOW, CONTENT_KINDS, CONTENT_OPEN, CONTENT_STATUSES, GATES, PRIORITIES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import ContentRequest, Department, User
from ..scopes import visible_content_requests
from ..security import access, dept_ok
from ..services import add_comment, comments_for, log_activity, notify, paginate
from ..timeutil import now
from ..ui import label

bp = Blueprint("creative", __name__, url_prefix="/creative")


def _team_dept():
    return Department.query.filter_by(slug="marketing").first()


def _team_members():
    """The content & design team: Marketing plus Content Creation, working as one team on requests."""
    ids = [d.id for d in Department.query.filter(Department.slug.in_(("marketing", "content"))).all()]
    return User.query.filter(User.active.is_(True), User.department_id.in_(ids)).order_by(User.name).all() if ids else []


def _get(rid):
    return visible_content_requests(current_user).filter(ContentRequest.id == rid).first_or_404()


@bp.route("/")
@access("creative.request")
def index():
    staff = dept_ok(current_user, "creative")
    box = request.args.get("box", "all" if staff else "mine")
    status = request.args.get("status", "open")
    kind = request.args.get("kind", "")
    text = request.args.get("q", "").strip()
    q = visible_content_requests(current_user)
    if box == "mine":
        q = q.filter(ContentRequest.requested_by_id == current_user.id)
    elif box == "assigned":
        q = q.filter(ContentRequest.assignee_id == current_user.id)
    elif box == "unassigned":
        q = q.filter(ContentRequest.assignee_id.is_(None), ContentRequest.status == "submitted")
    if status == "open":
        q = q.filter(ContentRequest.status.in_(CONTENT_OPEN))
    elif status != "all":
        q = q.filter(ContentRequest.status == status)
    if kind:
        q = q.filter(ContentRequest.kind == kind)
    if text:
        q = q.filter(or_(ContentRequest.title.ilike(f"%{text}%"), ContentRequest.brief.ilike(f"%{text}%")))
    pager = paginate(q.order_by(ContentRequest.due_date.is_(None), ContentRequest.due_date, ContentRequest.id.desc()), 20)
    base = visible_content_requests(current_user)
    counts = {"open": base.filter(ContentRequest.status.in_(CONTENT_OPEN)).count(),
              "review": base.filter(ContentRequest.status == "in_review").count(),
              "unassigned": base.filter(ContentRequest.status == "submitted", ContentRequest.assignee_id.is_(None)).count()}
    return render_template("creative/index.html", pager=pager, box=box, status=status, kind=kind, q=text, staff=staff,
                           kinds=CONTENT_KINDS, statuses=CONTENT_STATUSES, counts=counts)


@bp.route("/new", methods=["GET", "POST"])
@access("creative.request")
def new():
    form = Form([
        Field("title", "What do you need?", required=True, half=False, placeholder="e.g. Festive offer banner for social media"),
        Field("kind", "Type of work", "select", required=True, options=strings(CONTENT_KINDS)),
        Field("priority", "Priority", "select", required=True, options=PRIORITIES, value="medium"),
        Field("due_date", "Needed by", "date"),
        Field("reference_link", "Reference link", half=False, help="Optional: a folder, example or brand guide the designer should look at."),
        Field("brief", "Brief", "textarea", half=False, rows=6,
              help="Audience, message, sizes or formats, and anything that must (or must not) appear."),
    ])
    if request.method == "POST" and form.validate(request.form):
        team = _team_dept()
        req = ContentRequest(requested_by_id=current_user.id, requesting_department_id=current_user.department_id,
                             department_id=team.id if team else None, status="submitted", **form.data)
        db.session.add(req)
        db.session.flush()
        add_comment("content", req.id, f"Request submitted by {current_user.name}.", kind="status")
        log_activity("created", "content", req.id, f'Design/content request "{req.title}"')
        leads = [u for u in _team_members() if u.is_manager_level] or _team_members()
        notify(leads, "New design/content request", f'{current_user.name}: "{req.title}"', url_for("creative.detail", rid=req.id), kind="request")
        db.session.commit()
        flash("Request sent to the content and design team.", "success")
        return redirect(url_for("creative.detail", rid=req.id))
    return render_template("form_page.html", form=form, title="New design or content request",
                           subtitle="The Marketing content and creation team will pick it up and keep you updated.",
                           submit="Send request", cancel=url_for("creative.index"))


@bp.route("/<int:rid>")
@access("creative.request")
def detail(rid):
    req = _get(rid)
    steps = workflow.available(CONTENT_FLOW, req, current_user, "creative.manage")
    roles = workflow.actor_roles(req, current_user, "creative.manage")
    staff = dept_ok(current_user, "creative")
    return render_template("creative/detail.html", req=req, steps=steps, comments=comments_for("content", req.id),
                           can_assign="manager" in roles, can_claim=staff and req.assignee_id is None and req.status == "submitted",
                           team=_team_members(), can_edit=req.requested_by_id == current_user.id and req.status in ("submitted", "revision"))


@bp.route("/<int:rid>/edit", methods=["GET", "POST"])
@access("creative.request")
def edit(rid):
    req = _get(rid)
    if not (req.requested_by_id == current_user.id and req.status in ("submitted", "revision")):
        abort(403)
    form = Form([
        Field("title", "What do you need?", required=True, half=False),
        Field("kind", "Type of work", "select", required=True, options=strings(CONTENT_KINDS)),
        Field("priority", "Priority", "select", required=True, options=PRIORITIES),
        Field("due_date", "Needed by", "date"),
        Field("reference_link", "Reference link", half=False),
        Field("brief", "Brief", "textarea", half=False, rows=6),
    ]).fill(req)
    if request.method == "POST" and form.validate(request.form):
        for key, value in form.data.items():
            setattr(req, key, value)
        add_comment("content", req.id, "Request details updated.", kind="status")
        db.session.commit()
        flash("Request updated.", "success")
        return redirect(url_for("creative.detail", rid=req.id))
    return render_template("form_page.html", form=form, title="Edit request", subtitle=req.title, submit="Save changes",
                           cancel=url_for("creative.detail", rid=req.id))


@bp.route("/<int:rid>/transition", methods=["POST"])
@access("creative.request")
def transition(rid):
    req = _get(rid)
    step = workflow.find(CONTENT_FLOW, req, request.form.get("to", ""), current_user, "creative.manage")
    if step is None:
        abort(403)
    note = request.form.get("note", "").strip()
    link = url_for("creative.detail", rid=req.id)
    if step.note and not note:
        flash(f"Add a short note before you choose “{step.label}”.", "error")
        return redirect(link)
    req.status = step.dst
    req.delivered_at = now() if step.dst == "delivered" else req.delivered_at
    add_comment("content", req.id, f"{step.label}: status changed to {label(step.dst)}." + (f"\n{note}" if note else ""), kind="status")
    log_activity("status", "content", req.id, f'"{req.title}" → {label(step.dst)}')
    if step.dst in ("in_review", "delivered", "in_progress"):
        notify(req.requested_by, f"Your request is {label(step.dst).lower()}", f'"{req.title}"', link, kind="request")
    elif step.dst == "revision":
        notify(req.assignee, "Revision requested", f'"{req.title}": {note}', link, kind="revision")
    elif step.dst == "approved":
        notify(req.assignee, "Request approved", f'"{req.title}" was approved. Please deliver the final files.', link, kind="request")
    db.session.commit()
    flash(f"Request moved to {label(step.dst)}.", "success")
    return redirect(link)


@bp.route("/<int:rid>/assign", methods=["POST"])
@access("creative.request")
def assign(rid):
    req = _get(rid)
    if "manager" not in workflow.actor_roles(req, current_user, "creative.manage"):
        abort(403)
    person = next((u for u in _team_members() if u.id == request.form.get("assignee_id", type=int)), None)
    if person is None:
        flash("Choose someone from the content and design team.", "error")
    else:
        req.assignee_id = person.id
        add_comment("content", req.id, f"Assigned to {person.name}.", kind="status")
        notify([person, req.requested_by], "Design/content request assigned", f'"{req.title}" is assigned to {person.name}.',
               url_for("creative.detail", rid=req.id), kind="request")
        db.session.commit()
        flash(f"Assigned to {person.name}.", "success")
    return redirect(url_for("creative.detail", rid=req.id))


@bp.route("/<int:rid>/claim", methods=["POST"])
@access("creative.manage", "creative")
def claim(rid):
    req = _get(rid)
    if req.assignee_id is not None or req.status != "submitted":
        flash("Someone already picked this up.", "info")
    else:
        req.assignee_id = current_user.id
        add_comment("content", req.id, f"{current_user.name} picked up this request.", kind="status")
        notify(req.requested_by, "Your request was picked up", f'{current_user.name} is on "{req.title}".',
               url_for("creative.detail", rid=req.id), kind="request")
        db.session.commit()
        flash("This request is now yours.", "success")
    return redirect(url_for("creative.detail", rid=req.id))


@bp.route("/<int:rid>/comment", methods=["POST"])
@access("creative.request")
def comment(rid):
    req = _get(rid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("content", req.id, body)
        notify([req.assignee, req.requested_by], "New comment on a request", f'{current_user.name} on "{req.title}": {body[:120]}',
               url_for("creative.detail", rid=req.id), kind="comment")
        db.session.commit()
    return redirect(url_for("creative.detail", rid=req.id) + "#comments")
