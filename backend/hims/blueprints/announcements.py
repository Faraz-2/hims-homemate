from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..extensions import db
from ..forms import Form, Field
from ..models import Announcement, Department, User
from ..security import access, has_perm
from ..services import log_activity, notify, paginate

bp = Blueprint("announcements", __name__, url_prefix="/announcements")


def visible_announcements(user):
    q = Announcement.query
    if user.is_admin_level:
        return q
    return q.filter(or_(Announcement.department_id.is_(None), Announcement.department_id == user.department_id))


@bp.route("/")
@login_required
def index():
    pager = paginate(visible_announcements(current_user).order_by(Announcement.important.desc(),
                                                                  Announcement.created_at.desc()), 10)
    return render_template("announcements/index.html", pager=pager, can_post=has_perm(current_user, "announcements.post"))


@bp.route("/new", methods=["GET", "POST"])
@access("announcements.post")
def new():
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    form = Form([
        Field("title", "Title", required=True, half=False),
        Field("body", "Message", "textarea", required=True, half=False, rows=6),
        Field("department_id", "Audience", "select", options=[(d.id, d.name + " only") for d in depts],
              coerce=int, blank_label="Whole company", help="Leave as whole company to notify everyone."),
        Field("important", "Mark as important", "checkbox", help="Important announcements stay at the top."),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        ann = Announcement(title=d["title"], body=d["body"], important=d["important"],
                           department_id=d["department_id"], author_id=current_user.id)
        db.session.add(ann)
        db.session.flush()
        audience = User.query.filter_by(active=True)
        if d["department_id"]:
            audience = audience.filter_by(department_id=d["department_id"])
        notify(audience.all(), ("Important: " if d["important"] else "") + d["title"], d["body"][:200],
               url_for("announcements.index"), kind="announcement")
        log_activity("created", "announcement", ann.id, f'Posted announcement "{d["title"]}"')
        db.session.commit()
        flash("Announcement posted and staff notified.", "success")
        return redirect(url_for("announcements.index"))
    return render_template("form_page.html", form=form, title="New announcement",
                           subtitle="Share news with the company or a single department.",
                           submit="Post announcement", cancel=url_for("announcements.index"))


@bp.route("/<int:aid>/delete", methods=["POST"])
@access("announcements.post")
def delete(aid):
    ann = Announcement.query.get_or_404(aid)
    log_activity("deleted", "announcement", ann.id, f'Deleted announcement "{ann.title}"')
    db.session.delete(ann)
    db.session.commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("announcements.index"))
