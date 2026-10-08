"""'Roles and rights': a plain-English guide to who can do what, built from the live permission matrix."""
from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..constants import EMPLOYMENT_TYPES
from ..models import Role, User
from ..security import has_perm

bp = Blueprint("rights", __name__, url_prefix="/rights")

# (group, what people can do, permission code)
CAPABILITIES = [
    ("Everyday work", "Use their dashboard and see the employee directory", "dashboard.view"),
    ("Everyday work", "See and update their own tasks", "tasks.view"),
    ("Everyday work", "Create tasks for themselves", "tasks.create"),
    ("Everyday work", "Check in and out of work", "attendance.self"),
    ("Everyday work", "Request leave", "leaves.request"),
    ("Everyday work", "Send messages and use team chat", "messages.use"),
    ("Everyday work", "Raise IT tickets and ask questions", "tickets.create"),
    ("Everyday work", "Submit expenses", "finance.submit"),
    ("Everyday work", "Upload documents", "documents.upload"),
    ("Everyday work", "Send requests to other departments", "requests.create"),
    ("Leading a team", "Give tasks to other people (see the rule below)", "tasks.assign"),
    ("Leading a team", "Approve or send back submitted work", "tasks.approve"),
    ("Leading a team", "See the team's attendance", "attendance.team"),
    ("Leading a team", "Approve or reject leave", "leaves.approve"),
    ("Leading a team", "Assign and decide incoming department requests", "requests.manage"),
    ("Leading a team", "View and export MIS reports", "reports.view"),
    ("Managing people", "Add and manage employees in their own department", "employees.manage_team"),
    ("Managing people", "Post company announcements", "announcements.post"),
    ("Managing people", "Promote or move an employee back one rung on the career ladder", "employees.promote"),
    ("Managing people", "See every project company-wide (Project Management department)", "projects.view_all"),
    ("Managing people", "Create and manage marketing campaigns", "campaigns.manage"),
    ("Managing people", "Approve and pay expenses", "finance.approve"),
    ("Managing people", "Give someone a role in another department, or end it", "employees.departments"),
    ("Managing people", "Remove people from the team and hand over their work", "employees.remove"),
    ("Everyday work", "Upload files of any type to the Files area", "files.upload"),
    ("Everyday work", "Add, edit and price items on a vendor's product or menu list", "vendors.products"),
    ("Administration", "Add and manage all employees, and import from a spreadsheet", "employees.manage"),
    ("Administration", "Change system settings and take backups", "admin.settings"),
    ("Administration", "Create roles and edit the permission matrix", "admin.roles"),
]


@bp.route("/")
@login_required
def index():
    roles = Role.query.order_by(Role.level.desc(), Role.name).all()
    granted = {(r.id, p.code) for r in roles for p in r.permissions}
    counts = dict((r.id, User.query.filter_by(role_id=r.id, active=True).count()) for r in roles)
    groups = {}
    for group, text, code in CAPABILITIES:
        groups.setdefault(group, []).append({"text": text, "code": code, "has": {r.id: (r.key == "super_admin" or (r.id, code) in granted) for r in roles},
                                             "mine": has_perm(current_user, code)})
    return render_template("rights/index.html", roles=roles, groups=groups, counts=counts, mine=current_user.role,
                           personal=len(current_user.permission_overrides), etypes=EMPLOYMENT_TYPES)
