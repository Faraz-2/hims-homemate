from datetime import datetime, timedelta

import os

from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from ..constants import TASK_OPEN, TASK_STATUSES
from ..extensions import db
from ..models import (ActivityLog, Announcement, Attendance, ContentRequest, Department, Expense, InternalRequest, LeaveRequest, Lead, Task, Ticket, User)
from ..scopes import leaves_to_decide, visible_tasks
from ..security import dept_ok, has_perm
from ..services import database_size, lan_ip, list_backups
from ..timeutil import now, today
from .announcements import visible_announcements
from .attendance import day_summary
from .leaves import balances

bp = Blueprint("dashboard", __name__)

PIPELINE = ("todo", "in_progress", "in_review", "completed")


@bp.route("/")
@login_required
def index():
    u = current_user
    admin, manager = u.is_admin_level, u.is_manager_level
    t = today()
    first_day = t.replace(day=1)
    mine_open = Task.query.filter(Task.assignee_id == u.id, Task.status.in_(TASK_OPEN))
    mine = {"my_open": mine_open.count(), "my_overdue": mine_open.filter(Task.due_date < t).count(),
           "my_due_today": mine_open.filter(Task.due_date == t).count()}

    # things waiting for this person's decision
    approvals = {}
    review_q = Task.query.filter(Task.status == "in_review", Task.assignee_id != u.id)
    if not admin:
        from sqlalchemy import or_
        conds = [Task.created_by_id == u.id]
        if manager and u.department_id and has_perm(u, "tasks.approve"):
            conds.append(Task.department_id == u.department_id)
        review_q = review_q.filter(or_(*conds))
    approvals["tasks"] = review_q.count()
    approvals["leaves"] = leaves_to_decide(u).filter(LeaveRequest.status == "pending").count() if has_perm(u, "leaves.approve") else 0
    approvals["expenses"] = (Expense.query.filter(Expense.status == "submitted", Expense.submitted_by_id != u.id).count()
                             if has_perm(u, "finance.approve") and dept_ok(u, "finance") else 0)
    approvals["requests"] = (ContentRequest.query.filter(ContentRequest.status == "submitted", ContentRequest.assignee_id.is_(None)).count()
                             if manager and dept_ok(u, "creative") else 0)
    approvals["dept_requests"] = 0
    if has_perm(u, "requests.create"):
        approvals["dept_requests"] = InternalRequest.query.filter(InternalRequest.requester_id == u.id, InternalRequest.status == "in_review").count()
        if manager and u.department_id:
            approvals["dept_requests"] += InternalRequest.query.filter(InternalRequest.to_department_id == u.department_id,
                                                                       InternalRequest.status == "submitted", InternalRequest.assignee_id.is_(None)).count()
    approvals["tickets"] = 0
    for kind in ("csr", "it"):
        if dept_ok(u, kind) and not admin:
            approvals["tickets"] += Ticket.query.filter(Ticket.kind == kind, Ticket.status == "open", Ticket.assignee_id.is_(None)).count()
    approvals["total"] = sum(approvals.values())

    # task pipeline (chevron chain + segmented bar)
    scope = visible_tasks(u)
    counts = dict(scope.with_entities(Task.status, func.count(Task.id)).group_by(Task.status).all())
    pipeline = [{"key": k, "label": dict(TASK_STATUSES)[k], "count": counts.get(k, 0)} for k in PIPELINE]
    pipeline_extra = {"revision": counts.get("revision", 0), "cancelled": counts.get("cancelled", 0)}
    pipeline_total = sum(p["count"] for p in pipeline) + pipeline_extra["revision"]

    # attendance
    todays = Attendance.query.filter_by(user_id=u.id, work_date=t).first()
    team_users = None
    if admin:
        team_users = User.query.filter_by(active=True).all()
    elif manager and u.department_id:
        team_users = User.query.filter_by(active=True, department_id=u.department_id).all()
    att = day_summary(t, team_users) if team_users is not None else None

    # department performance
    dept_rows = []
    if manager:
        depts = Department.query.filter_by(active=True).order_by(Department.name)
        if not admin:
            depts = depts.filter(Department.id == u.department_id)
        month_ago = now() - timedelta(days=30)
        open_by = dict(db.session.query(Task.department_id, func.count(Task.id)).filter(Task.status.in_(TASK_OPEN)).group_by(Task.department_id).all())
        late_by = dict(db.session.query(Task.department_id, func.count(Task.id)).filter(Task.status.in_(TASK_OPEN), Task.due_date < t).group_by(Task.department_id).all())
        done_by = dict(db.session.query(Task.department_id, func.count(Task.id)).filter(Task.status == "completed", Task.completed_at >= month_ago).group_by(Task.department_id).all())
        heads = dict(db.session.query(User.department_id, func.count(User.id)).filter(User.active.is_(True)).group_by(User.department_id).all())
        for d in depts.all():
            o, dn = open_by.get(d.id, 0), done_by.get(d.id, 0)
            dept_rows.append({"dept": d, "members": heads.get(d.id, 0), "open": o, "overdue": late_by.get(d.id, 0), "done": dn,
                              "rate": round(100 * dn / (dn + o)) if (dn + o) else None})

    # executive KPIs (admin level)
    exec_kpi = None
    if admin:
        pipeline_value = db.session.query(func.coalesce(func.sum(Lead.value), 0)).filter(Lead.stage.notin_(("won", "lost"))).scalar()
        exec_kpi = {
            "employees": User.query.filter_by(active=True).count(),
            "open_tasks": Task.query.filter(Task.status.in_(TASK_OPEN)).count(),
            "open_tickets": Ticket.query.filter(Ticket.status.in_(("open", "in_progress", "waiting"))).count(),
            "spend": db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.status.in_(("approved", "paid")), Expense.expense_date >= first_day).scalar(),
            "pipeline": pipeline_value,
        }

    health = None
    if admin:
        backups = list_backups()
        health = {"database": db.engine.url.get_backend_name().replace("postgresql", "PostgreSQL").replace("sqlite", "SQLite"),
                  "db_size": database_size(), "last_backup": backups[0] if backups else None,
                  "online": User.query.filter(User.last_seen_at >= now() - timedelta(minutes=5)).count(),
                  "address": f"http://{lan_ip()}:{os.environ.get('HIMS_PORT', '8000')}",
                  "started": current_app.config["STARTED_AT"]}

    # my department + feed
    my_tasks = mine_open.order_by(Task.due_date.is_(None), Task.due_date).limit(6).all()
    announcements = visible_announcements(u).order_by(Announcement.important.desc(), Announcement.created_at.desc()).limit(3).all()
    feed_q = ActivityLog.query.filter(ActivityLog.action.notin_(("login", "logout", "login_failed", "check_in", "check_out")))
    if not admin:
        feed_q = feed_q.filter(ActivityLog.user_id == u.id) if not manager else feed_q.join(User, ActivityLog.user_id == User.id).filter(User.department_id == u.department_id)
    feed = feed_q.order_by(ActivityLog.created_at.desc()).limit(8).all()
    workspaces = Department.query.filter_by(active=True).order_by(Department.name).all() if admin else ([u.department] if u.department else [])
    hour = now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    return render_template("dashboard/index.html", mine=mine, approvals=approvals, pipeline=pipeline, pipeline_extra=pipeline_extra,
                           pipeline_total=pipeline_total, todays=todays, att=att, dept_rows=dept_rows, exec_kpi=exec_kpi,
                           my_tasks=my_tasks, health=health, announcements=announcements, feed=feed, workspaces=workspaces, greeting=greeting,
                           balances=balances(u) if has_perm(u, "leaves.request") else [], today=t)
