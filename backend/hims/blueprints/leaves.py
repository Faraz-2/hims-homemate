from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..constants import LEAVE_TYPES
from ..extensions import db
from ..forms import Field, Form
from ..models import LeaveRequest, User
from ..scopes import can_decide_leave, leaves_to_decide
from ..security import access, has_perm
from ..services import count_working_days, get_int, log_activity, notify, paginate
from ..timeutil import now, today

bp = Blueprint("leaves", __name__, url_prefix="/leaves")


def balances(user):
    year_start = today().replace(month=1, day=1)
    used = dict(db.session.query(LeaveRequest.leave_type, func.sum(LeaveRequest.days))
                .filter(LeaveRequest.user_id == user.id, LeaveRequest.status.in_(("approved", "pending")),
                        LeaveRequest.start_date >= year_start).group_by(LeaveRequest.leave_type).all())
    out = []
    for key, name in LEAVE_TYPES[:3]:
        allowed = get_int(f"leave_{key}", 0)
        taken = float(used.get(key, 0) or 0)
        out.append({"key": key, "name": name, "allowed": allowed, "used": taken, "left": max(allowed - taken, 0)})
    return out


@bp.route("/")
@access("leaves.request")
def index():
    tab = request.args.get("tab", "mine")
    approver = has_perm(current_user, "leaves.approve")
    pending_count = leaves_to_decide(current_user).filter(LeaveRequest.status == "pending").count() if approver else 0
    if tab == "approvals" and approver:
        status = request.args.get("status", "pending")
        q = leaves_to_decide(current_user)
        if status != "all":
            q = q.filter(LeaveRequest.status == status)
        pager = paginate(q.order_by(LeaveRequest.created_at.desc()), 15)
    else:
        tab = "mine"
        status = "all"
        pager = paginate(LeaveRequest.query.filter_by(user_id=current_user.id).order_by(LeaveRequest.created_at.desc()), 15)
    return render_template("leaves/index.html", pager=pager, tab=tab, status=status, approver=approver,
                           pending_count=pending_count, balances=balances(current_user),
                           can_decide=lambda l: l.status == "pending" and can_decide_leave(current_user, l))


@bp.route("/new", methods=["GET", "POST"])
@access("leaves.request")
def new():
    form = Form([
        Field("leave_type", "Leave type", "select", required=True, options=LEAVE_TYPES),
        Field("start_date", "First day", "date", required=True),
        Field("end_date", "Last day", "date", required=True),
        Field("reason", "Reason", "textarea", half=False, rows=3),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        days = count_working_days(d["start_date"], d["end_date"]) if d["end_date"] >= d["start_date"] else 0
        clash = LeaveRequest.query.filter(LeaveRequest.user_id == current_user.id,
                                          LeaveRequest.status.in_(("pending", "approved")),
                                          LeaveRequest.start_date <= d["end_date"],
                                          LeaveRequest.end_date >= d["start_date"]).first()
        if d["end_date"] < d["start_date"]:
            form.fields[2].error = "The last day can't be before the first day."
        elif days == 0:
            form.fields[1].error = "These dates contain no working days."
        elif clash:
            form.fields[1].error = "You already have leave requested for part of this period."
        else:
            leave = LeaveRequest(user_id=current_user.id, leave_type=d["leave_type"], start_date=d["start_date"],
                                 end_date=d["end_date"], days=days, reason=d["reason"])
            db.session.add(leave)
            db.session.flush()
            approvers = [u for u in User.query.filter_by(active=True).all()
                         if u.id != current_user.id and has_perm(u, "leaves.approve") and can_decide_leave(u, leave)]
            leave.user = current_user
            approvers = [u for u in approvers if can_decide_leave(u, leave)]
            notify(approvers, "Leave approval needed", f"{current_user.name} requested {days:g} day(s) of {leave.leave_type} leave.",
                   url_for("leaves.index", tab="approvals"), kind="approval")
            log_activity("created", "leave", leave.id, f"Requested {days:g} day(s) {leave.leave_type} leave")
            db.session.commit()
            flash(f"Leave request sent ({days:g} working day(s)).", "success")
            return redirect(url_for("leaves.index"))
    return render_template("form_page.html", form=form, title="Request leave",
                           subtitle="Working days are counted automatically from your company's working week.",
                           submit="Send request", cancel=url_for("leaves.index"))


@bp.route("/<int:lid>/decide", methods=["POST"])
@access("leaves.approve")
def decide(lid):
    leave = LeaveRequest.query.get_or_404(lid)
    if leave.status != "pending" or not can_decide_leave(current_user, leave):
        abort(403)
    decision = request.form.get("decision")
    if decision not in ("approved", "rejected"):
        abort(400)
    leave.status, leave.decided_by_id, leave.decided_at = decision, current_user.id, now()
    leave.decision_note = request.form.get("note", "").strip()[:300] or None
    notify(leave.user, f"Leave {decision}", f"Your {leave.leave_type} leave ({leave.start_date:%d %b} – {leave.end_date:%d %b}) was {decision} by {current_user.name}."
           + (f" Note: {leave.decision_note}" if leave.decision_note else ""), url_for("leaves.index"), kind="leave")
    log_activity(decision, "leave", leave.id, f"{decision.capitalize()} leave for {leave.user.name}")
    db.session.commit()
    flash(f"Leave {decision}.", "success")
    return redirect(url_for("leaves.index", tab="approvals"))


@bp.route("/<int:lid>/cancel", methods=["POST"])
@access("leaves.request")
def cancel(lid):
    leave = LeaveRequest.query.get_or_404(lid)
    if leave.user_id != current_user.id or leave.status != "pending":
        abort(403)
    leave.status = "cancelled"
    log_activity("cancelled", "leave", leave.id, "Cancelled own leave request")
    db.session.commit()
    flash("Leave request cancelled.", "success")
    return redirect(url_for("leaves.index"))
