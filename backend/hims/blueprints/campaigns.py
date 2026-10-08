"""Marketing campaigns: objective, target audience, platforms, content/media, performance, and linked tasks.

Shared by Marketing and Digital Sales & Marketing — both work from the same campaign, just with different
day-to-day focuses (Marketing plans and creates; Digital Sales drives leads and conversions from it).
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..constants import CAMPAIGN_OPEN, CAMPAIGN_PLATFORMS, CAMPAIGN_STATUSES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Campaign, Comment, Department, Lead, Task, User
from ..scopes import assignable_users
from ..security import access, dept_ok, has_perm
from ..services import add_comment, comments_for, log_activity, next_submission_version, notify, paginate, save_upload, upload_path

bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _visible():
    return Campaign.query if current_user.is_admin_level else Campaign.query.filter(
        or_(Campaign.department_id == current_user.department_id, Campaign.owner_id == current_user.id))


def _get(cid):
    return _visible().filter(Campaign.id == cid).first_or_404()


def _can_manage(campaign):
    return current_user.is_admin_level or campaign.owner_id == current_user.id or current_user.manages_department(campaign.department_id)


@bp.route("/")
@access("campaigns.view", "campaigns")
def index():
    status = request.args.get("status", "open")
    text = request.args.get("q", "").strip()
    q = _visible()
    if status == "open":
        q = q.filter(Campaign.status.in_(CAMPAIGN_OPEN))
    elif status != "all":
        q = q.filter(Campaign.status == status)
    if text:
        q = q.filter(Campaign.name.ilike(f"%{text}%"))
    pager = paginate(q.order_by(Campaign.end_date.is_(None), Campaign.end_date), 15)
    return render_template("campaigns/index.html", pager=pager, status=status, q=text, statuses=CAMPAIGN_STATUSES,
                           can_create=has_perm(current_user, "campaigns.manage"))


@bp.route("/new", methods=["GET", "POST"])
@access("campaigns.manage", "campaigns")
def new():
    depts = [d for d in Department.query.filter_by(active=True).order_by(Department.name).all() if d.slug in ("marketing", "digital-sales")]
    form = Form([
        Field("name", "Campaign name", required=True, half=False),
        Field("department_id", "Owning department", "select", required=True, coerce=int,
              options=[(d.id, d.name) for d in depts], value=current_user.department_id if current_user.department_id in {d.id for d in depts} else ""),
        Field("objective", "Objective", half=False, placeholder="What is this campaign meant to achieve?"),
        Field("target_audience", "Target audience", "textarea", half=False, rows=2),
        Field("platforms", "Platforms", "select", options=strings(CAMPAIGN_PLATFORMS), help="Add more from the campaign page after creating it."),
        Field("start_date", "Start date", "date"), Field("end_date", "End date", "date"),
        Field("budget", "Budget", "number", min=0, placeholder="0"),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        campaign = Campaign(status="planning", owner_id=current_user.id, **d)
        db.session.add(campaign)
        db.session.flush()
        log_activity("created", "campaign", campaign.id, f'Created campaign "{campaign.name}"')
        db.session.commit()
        flash("Campaign created.", "success")
        return redirect(url_for("campaigns.detail", cid=campaign.id))
    return render_template("form_page.html", form=form, title="New campaign",
                           subtitle="Plan a marketing campaign: audience, platforms, budget and timeline.",
                           submit="Create campaign", cancel=url_for("campaigns.index"))


@bp.route("/<int:cid>")
@access("campaigns.view", "campaigns")
def detail(cid):
    campaign = _get(cid)
    linked = Lead.query.filter_by(campaign_id=campaign.id)
    pipeline = {"leads": linked.count(), "won": linked.filter(Lead.stage == "won").count(),
                "open": linked.filter(Lead.stage.notin_(("won", "lost"))).count()}
    return render_template("campaigns/detail.html", campaign=campaign, comments=comments_for("campaign", campaign.id),
                           can_manage=_can_manage(campaign), tasks=campaign.tasks, pipeline=pipeline)


@bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@access("campaigns.manage", "campaigns")
def edit(cid):
    campaign = _get(cid)
    if not _can_manage(campaign):
        abort(403)
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    form = Form([
        Field("name", "Campaign name", required=True, half=False),
        Field("department_id", "Owning department", "select", required=True, coerce=int, options=[(d.id, d.name) for d in depts]),
        Field("status", "Status", "select", required=True, options=CAMPAIGN_STATUSES),
        Field("objective", "Objective", half=False),
        Field("target_audience", "Target audience", "textarea", half=False, rows=2),
        Field("platforms", "Platforms (comma-separated)", half=False),
        Field("start_date", "Start date", "date"), Field("end_date", "End date", "date"),
        Field("budget", "Budget", "number", min=0),
    ]).fill(campaign)
    if request.method == "POST" and form.validate(request.form):
        for key, value in form.data.items():
            setattr(campaign, key, value)
        log_activity("updated", "campaign", campaign.id, f'Updated campaign "{campaign.name}"')
        db.session.commit()
        flash("Campaign saved.", "success")
        return redirect(url_for("campaigns.detail", cid=campaign.id))
    return render_template("form_page.html", form=form, title="Edit campaign", subtitle=campaign.name,
                           submit="Save changes", cancel=url_for("campaigns.detail", cid=campaign.id))


@bp.route("/<int:cid>/metrics", methods=["POST"])
@access("campaigns.manage", "campaigns")
def metrics(cid):
    campaign = _get(cid)
    if not _can_manage(campaign):
        abort(403)
    for field in ("reach", "leads_generated", "qualified_leads", "conversions", "engagement"):
        value = request.form.get(field, type=int)
        if value is not None and value >= 0:
            setattr(campaign, field, value)
    spend = request.form.get("spend", type=float)
    if spend is not None and spend >= 0:
        campaign.spend = spend
    log_activity("updated", "campaign", campaign.id, f'Logged performance for "{campaign.name}"')
    db.session.commit()
    flash("Performance updated.", "success")
    return redirect(url_for("campaigns.detail", cid=campaign.id))


@bp.route("/<int:cid>/media", methods=["POST"])
@access("campaigns.view", "campaigns")
def media_upload(cid):
    """Upload a piece of content or creative against the campaign — kept as a new version each time."""
    campaign = _get(cid)
    upload = request.files.get("file")
    note = request.form.get("note", "").strip()
    link = url_for("campaigns.detail", cid=campaign.id) + "#comments"
    if not upload or not upload.filename:
        flash("Choose a file to upload.", "error")
        return redirect(link)
    try:
        stored, original, _size = save_upload(upload, "campaigns")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(link)
    version = next_submission_version("campaign", campaign.id)
    db.session.add(Comment(entity_type="campaign", entity_id=campaign.id, user_id=current_user.id, kind="submission",
                           body=note or f"Content v{version}: {original}", attachment_file=stored,
                           attachment_name=original, version=version))
    log_activity("uploaded", "campaign", campaign.id, f'{current_user.name} uploaded "{original}" to "{campaign.name}"')
    db.session.commit()
    flash(f"Uploaded as version {version}.", "success")
    return redirect(link)


@bp.route("/media/<int:cid>/file")
@access("campaigns.view", "campaigns")
def media_file(cid):
    c = db.session.get(Comment, cid)
    if c is None or c.entity_type != "campaign" or not c.attachment_file:
        abort(404)
    _get(c.entity_id)
    try:
        path = upload_path(c.attachment_file)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=c.attachment_name or path.name)


@bp.route("/<int:cid>/comment", methods=["POST"])
@access("campaigns.view", "campaigns")
def comment(cid):
    campaign = _get(cid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("campaign", campaign.id, body)
        db.session.commit()
    return redirect(url_for("campaigns.detail", cid=campaign.id) + "#comments")
