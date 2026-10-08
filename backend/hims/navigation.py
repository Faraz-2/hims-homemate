"""Sidebar navigation and the values every page template needs."""
from flask import g, request
from flask_login import current_user

from .extensions import db
from .models import Notification
from .security import can_view_reports, dept_ok, has_perm
from .services import get_setting


def build_nav(user):
    P = lambda code: has_perm(user, code)  # noqa: E731
    sections = [
        ("Overview", [
            ("Dashboard", "dashboard.index", {}, "home", "dashboard", True),
            ("Announcements", "announcements.index", {}, "megaphone", "announcements", True),
            ("Roles and rights", "rights.index", {}, "shield", "rights", True),
        ]),
        ("My work", [
            ("Tasks", "tasks.index", {}, "check-square", "tasks", P("tasks.view")),
            ("Attendance", "attendance.index", {}, "clock", "attendance", P("attendance.self")),
            ("Leave", "leaves.index", {}, "calendar", "leaves", P("leaves.request")),
            ("Documents", "documents.index", {}, "folder", "documents", P("documents.view")),
            ("Files", "files.index", {}, "archive", "files", P("files.view")),
            ("Projects", "projects.index", {}, "layers", "projects", P("projects.view")),
            ("Campaigns", "campaigns.index", {}, "megaphone", "campaigns", P("campaigns.view") and dept_ok(user, "campaigns")),
            ("Messages", "messages.index", {}, "message", "messages", P("messages.use")),
            ("Department requests", "dept_requests.index", {}, "arrow-right", "dept_requests", P("requests.create")),
        ]),
        ("Departments", [
            ("All departments", "departments.index", {}, "building", "departments", P("departments.view")),
            ("My workspace", "departments.workspace", {"slug": user.dept_slug or "-"}, "briefcase", "departments", bool(user.dept_slug)),
        ]),
        ("Business", [
            ("Lead pipeline", "leads.board", {}, "trending-up", "leads", P("leads.view") and dept_ok(user, "leads")),
            ("Recruitment", "recruitment.board", {}, "user-check", "recruitment", P("recruitment.view") and dept_ok(user, "recruitment")),
            ("NIC Hyderabad pipeline", "incubation.board", {}, "rocket", "incubation", P("incubation.view") and dept_ok(user, "incubation")),
            ("Design and content", "creative.index", {}, "pen-tool", "creative", P("creative.request")),
            ("Expenses", "finance.index", {}, "dollar", "finance", P("finance.submit")),
            ("Vendors", "vendors.index", {}, "truck", "vendors", P("vendors.view") and dept_ok(user, "vendors")),
        ]),
        ("Helpdesk", [
            ("CSR helpdesk", "helpdesk.index", {"kind": "csr"}, "headphones", "helpdesk", dept_ok(user, "csr")),
            ("IT helpdesk", "helpdesk.index", {"kind": "it"}, "monitor", "helpdesk", P("tickets.create")),
            ("Queries", "helpdesk.index", {"kind": "queries"}, "help", "helpdesk", P("tickets.create")),
        ]),
        ("Insights", [
            ("Reports and MIS", "reports.index", {}, "bar-chart", "reports", can_view_reports(user)),
            ("Employees", "employees.index", {}, "users", "employees", P("employees.view")),
        ]),
        ("Administration", [
            ("Admin", "admin.index", {}, "settings", "admin",
             any(P(c) for c in ("admin.settings", "admin.roles", "admin.backup", "activity.view"))),
        ]),
    ]
    view_args = request.view_args or {}

    def active(endpoint, params, prefix):
        bp = request.blueprint
        if prefix == "helpdesk":
            kind = view_args.get("kind") or g.get("helpdesk_kind")
            return bp == "helpdesk" and kind == params.get("kind")
        if prefix == "departments":
            own = request.endpoint == "departments.workspace" and view_args.get("slug") == user.dept_slug
            return own if endpoint == "departments.workspace" else (bp == "departments" and not own)
        if prefix == "vendors":
            return bp in ("vendors", "vendor_products")
        return bp == prefix

    out = []
    for title, items in sections:
        rows = [{"label": l, "endpoint": e, "params": p, "icon": i, "active": active(e, p, pre)}
                for (l, e, p, i, pre, show) in items if show]
        if rows:
            out.append({"title": title, "links": rows})
    return out


def register_context(app):
    @app.context_processor
    def inject():
        ctx = {"has_perm": has_perm, "dept_ok": dept_ok, "app_version": app.config["VERSION"]}
        if current_user.is_authenticated:
            ctx["nav"] = build_nav(current_user)
            ctx["company_name"] = get_setting("company_name", "Home Mate")
            ctx["unread_count"] = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            from .blueprints.messages import unread_total
            ctx["unread_messages"] = unread_total(current_user) if has_perm(current_user, "messages.use") else 0
            ctx["recent_notifications"] = (Notification.query.filter_by(user_id=current_user.id)
                                           .order_by(Notification.created_at.desc()).limit(25).all())     # the drop-down scrolls
        else:
            ctx["company_name"] = "Home Mate"
        return ctx
