"""MIS & reporting. Operations (Excel/data analysts) and admins see company-wide; heads see their department."""
from datetime import datetime, timedelta

from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from sqlalchemy import case, func

from ..constants import (CANDIDATE_STAGES, LEAD_STAGES, LEAVE_TYPES, TASK_OPEN, TASK_STATUSES, TICKET_KINDS)
from ..extensions import db
from ..models import (Attendance, Candidate, Department, Expense, LeaveRequest, Lead, Task, Ticket, User)
from ..scopes import visible_expenses, visible_tasks, visible_tickets
from ..security import access, can_view_reports, dept_ok, has_perm, is_mis_user
from ..services import csv_response
from ..timeutil import today
from ..ui import label

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _guard():
    if not can_view_reports(current_user):
        abort(403)


def _period():
    days = request.args.get("days", 30, type=int)
    days = days if days in (7, 30, 90, 365) else 30
    return days, today() - timedelta(days=days - 1)


def _scope():
    """(company_wide, department_id or None)"""
    if is_mis_user(current_user):
        return True, request.args.get("dept", type=int)
    return False, current_user.department_id


def _users(dept_id):
    q = User.query.filter(User.active.is_(True))
    return q.filter(User.department_id == dept_id) if dept_id else q


def _tasks(dept_id):
    q = Task.query if is_mis_user(current_user) else visible_tasks(current_user)
    return q.filter(Task.department_id == dept_id) if dept_id else q


def _expenses():
    return Expense.query if is_mis_user(current_user) else visible_expenses(current_user)


def _tickets():
    return Ticket.query if is_mis_user(current_user) else visible_tickets(current_user)


def _sections():
    mis = is_mis_user(current_user)
    return {
        "expenses": mis or dept_ok(current_user, "finance") or current_user.role.level >= 60,
        "leads": mis or dept_ok(current_user, "leads"),
        "tickets": mis or dept_ok(current_user, "csr") or dept_ok(current_user, "it"),
        "recruitment": mis or dept_ok(current_user, "recruitment"),
    }


@bp.route("/")
@access()
def index():
    _guard()
    days, start = _period()
    wide, dept_id = _scope()
    show = _sections()
    start_dt = datetime.combine(start, datetime.min.time())
    data = {}

    # ---- tasks
    tq = _tasks(dept_id)
    in_period = tq.filter(Task.created_at >= start_dt)
    by_status = dict(in_period.with_entities(Task.status, func.count(Task.id)).group_by(Task.status).all())
    data["task_status"] = [(label(k), by_status.get(k, 0)) for k, _ in TASK_STATUSES]
    data["tasks_created"] = in_period.count()
    data["tasks_done"] = tq.filter(Task.status == "completed", Task.completed_at >= start_dt).count()
    data["tasks_open"] = tq.filter(Task.status.in_(TASK_OPEN)).count()
    data["tasks_overdue"] = tq.filter(Task.status.in_(TASK_OPEN), Task.due_date < today()).count()
    done = tq.filter(Task.status == "completed", Task.completed_at >= start_dt, Task.due_date.isnot(None)).all()
    data["on_time"] = round(100 * sum(1 for t in done if t.completed_at.date() <= t.due_date) / len(done)) if done else None
    weeks = []
    for i in range(7, -1, -1):
        w_start = today() - timedelta(days=today().weekday() + 7 * i)
        w_end = w_start + timedelta(days=7)
        n = tq.filter(Task.status == "completed", Task.completed_at >= datetime.combine(w_start, datetime.min.time()),
                      Task.completed_at < datetime.combine(w_end, datetime.min.time())).count()
        weeks.append((w_start.strftime("%d %b"), n))
    data["weekly_done"] = weeks

    if wide:
        rows = []
        for d in Department.query.filter_by(active=True).order_by(Department.name).all():
            base = Task.query.filter(Task.department_id == d.id)
            rows.append({"dept": d, "members": User.query.filter_by(active=True, department_id=d.id).count(),
                         "created": base.filter(Task.created_at >= start_dt).count(),
                         "done": base.filter(Task.status == "completed", Task.completed_at >= start_dt).count(),
                         "open": base.filter(Task.status.in_(TASK_OPEN)).count(),
                         "overdue": base.filter(Task.status.in_(TASK_OPEN), Task.due_date < today()).count()})
        data["dept_rows"] = rows

    # ---- attendance
    users = _users(dept_id).order_by(User.name).all()
    ids = [u.id for u in users] or [0]
    att = (db.session.query(Attendance.user_id, func.count(Attendance.id),
                            func.sum(case((Attendance.status == "late", 1), else_=0)))
           .filter(Attendance.user_id.in_(ids), Attendance.work_date >= start).group_by(Attendance.user_id).all())
    att_map = {uid: (n, late or 0) for uid, n, late in att}
    hours = {}
    for r in Attendance.query.filter(Attendance.user_id.in_(ids), Attendance.work_date >= start, Attendance.check_out.isnot(None)).all():
        hours.setdefault(r.user_id, []).append(r.hours or 0)
    data["attendance"] = [{"user": u, "present": att_map.get(u.id, (0, 0))[0], "late": att_map.get(u.id, (0, 0))[1],
                           "avg_hours": round(sum(hours[u.id]) / len(hours[u.id]), 1) if u.id in hours else None} for u in users][:80]

    # ---- leave
    lq = LeaveRequest.query.filter(LeaveRequest.status == "approved", LeaveRequest.start_date >= start,
                                   LeaveRequest.user_id.in_(ids))
    used = dict(lq.with_entities(LeaveRequest.leave_type, func.sum(LeaveRequest.days)).group_by(LeaveRequest.leave_type).all())
    data["leave"] = [(name, float(used.get(k, 0) or 0)) for k, name in LEAVE_TYPES]

    # ---- headcount
    if wide:
        hc = dict(db.session.query(User.department_id, func.count(User.id)).filter(User.active.is_(True)).group_by(User.department_id).all())
        data["headcount"] = [(d.name, hc.get(d.id, 0)) for d in Department.query.filter_by(active=True).order_by(Department.name).all()]

    # ---- expenses
    if show["expenses"]:
        eq = _expenses().filter(Expense.expense_date >= start, Expense.status.in_(("approved", "paid")))
        if dept_id:
            eq = eq.filter(Expense.department_id == dept_id)
        data["exp_total"] = eq.with_entities(func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
        data["exp_category"] = [(c or "Other", float(a or 0)) for c, a in
                                eq.with_entities(Expense.category, func.sum(Expense.amount)).group_by(Expense.category)
                                .order_by(func.sum(Expense.amount).desc()).all()]
        data["exp_pending"] = _expenses().filter(Expense.status == "submitted").count()

    # ---- leads
    if show["leads"]:
        rows = dict((s, (n, v or 0)) for s, n, v in db.session.query(Lead.stage, func.count(Lead.id), func.sum(Lead.value)).group_by(Lead.stage).all())
        data["lead_stages"] = [(name, rows.get(k, (0, 0))[0], rows.get(k, (0, 0))[1]) for k, name in LEAD_STAGES]
        won, lost = rows.get("won", (0, 0))[0], rows.get("lost", (0, 0))[0]
        data["win_rate"] = round(100 * won / (won + lost)) if (won + lost) else None
        data["pipeline_value"] = sum(v for k, (n, v) in rows.items() if k not in ("won", "lost"))

    # ---- tickets
    if show["tickets"]:
        tk = _tickets().filter(Ticket.created_at >= start_dt)
        data["ticket_kind"] = [(TICKET_KINDS.get(k, k), n) for k, n in tk.with_entities(Ticket.kind, func.count(Ticket.id)).group_by(Ticket.kind).all()]
        data["ticket_status"] = [(label(k), n) for k, n in tk.with_entities(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status).all()]
        solved = tk.filter(Ticket.resolved_at.isnot(None)).all()
        data["ticket_hours"] = round(sum((t.resolved_at - t.created_at).total_seconds() for t in solved) / 3600 / len(solved), 1) if solved else None

    # ---- recruitment
    if show["recruitment"]:
        rows = dict(db.session.query(Candidate.stage, func.count(Candidate.id)).group_by(Candidate.stage).all())
        data["funnel"] = [(name, rows.get(k, 0)) for k, name in CANDIDATE_STAGES]

    return render_template("reports/index.html", d=data, days=days, wide=wide, dept=dept_id, show=show,
                           departments=Department.query.order_by(Department.name).all(),
                           can_export=has_perm(current_user, "reports.export") or current_user.dept_slug == "operations",
                           scope_name=(getattr(db.session.get(Department, dept_id), "name", "All departments") if dept_id else "All departments"))


EXPORTS = ("tasks", "attendance", "leaves", "expenses", "leads", "tickets", "candidates", "employees")


@bp.route("/export/<name>.csv")
@access()
def export(name):
    _guard()
    if not (has_perm(current_user, "reports.export") or current_user.dept_slug == "operations"):
        abort(403)
    if name not in EXPORTS:
        abort(404)
    days, start = _period()
    wide, dept_id = _scope()
    show = _sections()
    start_dt = datetime.combine(start, datetime.min.time())
    users = _users(dept_id).all()
    ids = [u.id for u in users] or [0]
    stamp = today().isoformat()

    if name == "tasks":
        q = _tasks(dept_id).filter(Task.created_at >= start_dt).order_by(Task.id)
        return csv_response(f"tasks_{stamp}.csv", ["ID", "Title", "Department", "Assignee", "Created by", "Priority", "Status", "Due date", "Created", "Completed"],
                            [[t.id, t.title, t.department.name if t.department else "", t.assignee.name if t.assignee else "", t.created_by.name,
                              label(t.priority), label(t.status), t.due_date or "", t.created_at.date(), t.completed_at.date() if t.completed_at else ""] for t in q])
    if name == "attendance":
        q = Attendance.query.filter(Attendance.user_id.in_(ids), Attendance.work_date >= start).order_by(Attendance.work_date, Attendance.user_id)
        return csv_response(f"attendance_{stamp}.csv", ["Date", "Employee", "Department", "Check in", "Check out", "Hours", "Status"],
                            [[a.work_date, a.user.name, a.user.department.name if a.user.department else "", a.check_in.strftime("%H:%M") if a.check_in else "",
                              a.check_out.strftime("%H:%M") if a.check_out else "", a.hours or "", label(a.status)] for a in q])
    if name == "leaves":
        q = LeaveRequest.query.filter(LeaveRequest.user_id.in_(ids), LeaveRequest.start_date >= start).order_by(LeaveRequest.start_date)
        return csv_response(f"leaves_{stamp}.csv", ["Employee", "Department", "Type", "From", "To", "Days", "Status", "Decided by"],
                            [[l.user.name, l.user.department.name if l.user.department else "", label(l.leave_type), l.start_date, l.end_date, l.days,
                              label(l.status), l.decided_by.name if l.decided_by else ""] for l in q])
    if name == "expenses":
        if not show["expenses"]:
            abort(403)
        q = _expenses().filter(Expense.expense_date >= start)
        if dept_id:
            q = q.filter(Expense.department_id == dept_id)
        return csv_response(f"expenses_{stamp}.csv", ["ID", "Date", "Title", "Category", "Amount", "Department", "Submitted by", "Vendor", "Status", "Paid on"],
                            [[e.id, e.expense_date, e.title, e.category, e.amount, e.department.name if e.department else "", e.submitted_by.name,
                              e.vendor.name if e.vendor else "", label(e.status), e.paid_on or ""] for e in q.order_by(Expense.expense_date)])
    if name == "leads":
        if not show["leads"]:
            abort(403)
        return csv_response(f"leads_{stamp}.csv", ["ID", "Name", "Company", "Phone", "Email", "Source", "Stage", "Value", "Owner", "Follow-up"],
                            [[l.id, l.name, l.company, l.phone, l.email, l.source, label(l.stage), l.value, l.owner.name if l.owner else "", l.next_follow_up or ""]
                             for l in Lead.query.order_by(Lead.id)])
    if name == "tickets":
        if not show["tickets"]:
            abort(403)
        q = _tickets().filter(Ticket.created_at >= start_dt).order_by(Ticket.id)
        return csv_response(f"tickets_{stamp}.csv", ["Number", "Type", "Subject", "Category", "Priority", "Status", "Raised by", "Assignee", "Created", "Resolved"],
                            [[t.number, t.kind, t.subject, t.category, label(t.priority), label(t.status), t.requester.name,
                              t.assignee.name if t.assignee else "", t.created_at.date(), t.resolved_at.date() if t.resolved_at else ""] for t in q])
    if name == "candidates":
        if not show["recruitment"]:
            abort(403)
        return csv_response(f"candidates_{stamp}.csv", ["Name", "Position", "Stage", "Source", "Expected salary", "Recruiter"],
                            [[c.name, c.position, label(c.stage), c.source, c.expected_salary, c.owner.name if c.owner else ""] for c in Candidate.query.order_by(Candidate.id)])
    # employees: contact details, admins only
    if not has_perm(current_user, "employees.manage"):
        abort(403)
    return csv_response(f"employees_{stamp}.csv", ["Code", "Name", "Email", "Phone", "Department", "Role", "Title", "Joined"],
                        [[u.employee_code, u.name, u.email, u.phone, u.department.name if u.department else "", u.role.name, u.job_title, u.joined_on or ""]
                         for u in User.query.filter_by(active=True).order_by(User.name)])
