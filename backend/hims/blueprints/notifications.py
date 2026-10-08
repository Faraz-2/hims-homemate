from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Notification
from ..services import paginate

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@bp.route("/")
@login_required
def index():
    show = request.args.get("show", "all")
    q = Notification.query.filter_by(user_id=current_user.id)
    if show == "unread":
        q = q.filter_by(is_read=False)
    pager = paginate(q.order_by(Notification.created_at.desc()), 25)
    return render_template("notifications/index.html", pager=pager, show=show)


@bp.route("/<int:nid>/open")
@login_required
def open_one(nid):
    n = Notification.query.filter_by(id=nid, user_id=current_user.id).first_or_404()
    n.is_read = True
    db.session.commit()
    target = n.url if (n.url and n.url.startswith("/") and not n.url.startswith("//")) else url_for("notifications.index")
    return redirect(target)


@bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(request.referrer or url_for("notifications.index"))
