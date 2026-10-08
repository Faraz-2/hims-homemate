from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..models import Attendance, Department, LeaveRequest, User
from ..security import access
from ..services import get_int, get_setting, is_working_day, log_activity
from ..timeutil import now, today

bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _parse_time(text, fallback):
    try:
        h, m = str(text).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return fallback


def late_after():
    """Time of day after which a check-in counts as late."""
    h, m = _parse_time(get_setting("work_start", "09:00"), (9, 0))
    return datetime.combine(today(), datetime.min.time()) + timedelta(hours=h, minutes=m + get_int("late_grace_minutes", 15))


def day_summary(day, users):
    """Present / late / on leave / absent counts for a list of users on one day."""
    ids = [u.id for u in users]
    recs = Attendance.query.filter(Attendance.work_date == day, Attendance.user_id.in_(ids or [0])).all()
    present_ids = {r.user_id for r in recs}
    leave_ids = {l.user_id for l in LeaveRequest.query.filter(
        LeaveRequest.status == "approved", LeaveRequest.start_date <= day, LeaveRequest.end_date >= day,
        LeaveRequest.user_id.in_(ids or [0])).all()}
    on_leave = len(leave_ids - present_ids)
    absent = 0
    if is_working_day(day) and (day < today() or (day == today() and now() > late_after())):
        absent = max(0, len(ids) - len(present_ids) - on_leave)
    return {"total": len(ids), "present": len(present_ids), "late": sum(1 for r in recs if r.status == "late"),
            "on_leave": on_leave, "absent": absent, "records": recs, "present_ids": present_ids, "leave_ids": leave_ids}


@bp.route("/")
@access("attendance.self")
def index():
    month = request.args.get("month", "")
    try:
        first = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError:
        first = today().replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    records = (Attendance.query.filter(Attendance.user_id == current_user.id, Attendance.work_date >= first,
                                       Attendance.work_date < nxt).order_by(Attendance.work_date.desc()).all())
    todays = Attendance.query.filter_by(user_id=current_user.id, work_date=today()).first()
    hours = [r.hours for r in records if r.hours]
    stats = {"days": len(records), "late": sum(1 for r in records if r.status == "late"),
             "avg_hours": round(sum(hours) / len(hours), 1) if hours else 0}
    prev_month = (first - timedelta(days=1)).replace(day=1)
    return render_template("attendance/index.html", todays=todays, records=records, stats=stats, first=first,
                           prev_month=prev_month, next_month=nxt if nxt <= today() else None,
                           work_start=get_setting("work_start", "09:00"), work_end=get_setting("work_end", "18:00"))


@bp.route("/check-in", methods=["POST"])
@access("attendance.self")
def check_in():
    if Attendance.query.filter_by(user_id=current_user.id, work_date=today()).first():
        flash("You have already checked in today.", "info")
        return redirect(request.referrer or url_for("attendance.index"))
    stamp = now()
    status = "late" if stamp > late_after() else "present"
    db.session.add(Attendance(user_id=current_user.id, work_date=today(), check_in=stamp, status=status))
    log_activity("check_in", "attendance", None, f"{current_user.name} checked in ({status})")
    db.session.commit()
    flash(f"Checked in at {stamp:%I:%M %p}." + (" You are marked late." if status == "late" else " Have a good day!"), "success")
    return redirect(request.referrer or url_for("attendance.index"))


@bp.route("/check-out", methods=["POST"])
@access("attendance.self")
def check_out():
    rec = Attendance.query.filter_by(user_id=current_user.id, work_date=today()).first()
    if rec is None:
        flash("Check in first, then you can check out.", "error")
    elif rec.check_out:
        flash("You have already checked out today.", "info")
    else:
        rec.check_out = now()
        log_activity("check_out", "attendance", rec.id, f"{current_user.name} checked out")
        db.session.commit()
        flash(f"Checked out at {rec.check_out:%I:%M %p}. Worked {rec.hours} hours.", "success")
    return redirect(request.referrer or url_for("attendance.index"))


@bp.route("/team")
@access("attendance.team")
def team():
    try:
        day = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        day = today()
    dept_id = request.args.get("dept", type=int)
    q = User.query.filter(User.active.is_(True))
    if current_user.is_admin_level:
        if dept_id:
            q = q.filter(User.department_id == dept_id)
    else:
        q = q.filter(User.department_id == current_user.department_id)
        dept_id = current_user.department_id
    users = q.order_by(User.name).all()
    summary = day_summary(day, users)
    by_user = {r.user_id: r for r in summary["records"]}
    rows = []
    for u in users:
        rec = by_user.get(u.id)
        if rec:
            state = rec.status
        elif u.id in summary["leave_ids"]:
            state = "on_leave"
        elif is_working_day(day) and (day < today() or (day == today() and now() > late_after())):
            state = "absent"
        else:
            state = "pending" if is_working_day(day) else "off"
        rows.append((u, rec, state))
    return render_template("attendance/team.html", rows=rows, summary=summary, day=day, dept=dept_id,
                           departments=Department.query.order_by(Department.name).all(),
                           prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1) if day < today() else None)
