"""Database models for HIMS.

Written for SQLite (default) and PostgreSQL: only portable column types are used.
"""
from flask import g
from flask_login import UserMixin
from sqlalchemy import true
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .timeutil import now, today

from .constants import CAMPAIGN_OPEN, TASK_OPEN
TASK_OPEN_STATUSES = set(TASK_OPEN)
CAMPAIGN_OPEN_STATUSES = set(CAMPAIGN_OPEN)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)


# ------------------------------------------------------------------ access control
class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    level = db.Column(db.Integer, nullable=False, default=20)
    description = db.Column(db.String(255))
    permissions = db.relationship("Permission", secondary=role_permissions, back_populates="roles")
    users = db.relationship("User", back_populates="role")


class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False)
    label = db.Column(db.String(160), nullable=False)
    module = db.Column(db.String(60), nullable=False)
    roles = db.relationship("Role", secondary=role_permissions, back_populates="permissions")


class UserDepartmentRole(db.Model):
    """A secondary affiliation: this person also holds a role in another department, alongside their main one.

    Example: Hamza Khan is Operations Head and also Co-Head of Sales. Being a member appears on that department's
    roster and lets him open its workspace. A secondary role at Team Lead level or above also lets him *manage* that
    department (assign and review its tasks), but only with the rights his MAIN role already gives him: the
    permission matrix always follows role_id, never the secondary roles.
    """
    __tablename__ = "user_department_roles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    department = db.relationship("Department")
    role = db.relationship("Role")
    __table_args__ = (db.UniqueConstraint("user_id", "department_id", name="uq_user_department_role"),)


class UserPermission(db.Model):
    """One person's exception to their role: always allow or always deny a single right."""
    __tablename__ = "user_permissions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    permission_id = db.Column(db.Integer, db.ForeignKey("permissions.id"), nullable=False)
    allow = db.Column(db.Boolean, nullable=False)
    permission = db.relationship("Permission")
    __table_args__ = (db.UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),)


# ------------------------------------------------------------------ organisation
class Department(db.Model):
    __tablename__ = "departments"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(400))
    functions = db.Column(db.String(300))          # comma separated sub-functions
    icon = db.Column(db.String(30), default="building")
    active = db.Column(db.Boolean, default=True, nullable=False)
    members = db.relationship("User", back_populates="department", order_by="User.name")

    @property
    def function_list(self):
        return [f.strip() for f in (self.functions or "").split(",") if f.strip()]

    @property
    def heads(self):
        return [u for u in self.members if u.active and u.role and 60 <= u.role.level < 80]

    @property
    def co_heads(self):
        primary = [u for u in self.members if u.active and u.role and u.role.key == "co_head"]
        secondary = [r.user for r in UserDepartmentRole.query.filter_by(department_id=self.id).all()
                     if r.user.active and r.role.key == "co_head" and r.user not in primary]
        return primary + secondary


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    job_title = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    joined_on = db.Column(db.Date)
    active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    last_seen_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    employment_type = db.Column(db.String(20), default="full_time", nullable=False, server_default="full_time")
    reports_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    display_title = db.Column(db.String(80))          # e.g. "Founder" for someone who holds the Super Admin role
                                                        # but whose real-world title is something else
    # 1.5: False = "paused": the person stays in HIMS but no longer appears in anyone's "Assign to" list.
    task_assignable = db.Column(db.Boolean, default=True, nullable=False, server_default=true())

    role = db.relationship("Role", back_populates="users")
    reports_to = db.relationship("User", remote_side=[id], backref="direct_reports")
    permission_overrides = db.relationship("UserPermission", cascade="all, delete-orphan")
    secondary_roles = db.relationship("UserDepartmentRole", foreign_keys="UserDepartmentRole.user_id",
                                      backref="user", cascade="all, delete-orphan", order_by="UserDepartmentRole.id")
    department = db.relationship("Department", back_populates="members")

    # -- passwords
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # -- flask-login: an inactive user cannot sign in or keep a session
    @property
    def is_active(self):  # noqa: D401
        return bool(self.active)

    # -- convenience
    @property
    def initials(self):
        parts = [p for p in (self.name or "?").split() if p]
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @property
    def is_super(self):
        return self.role.key == "super_admin"

    @property
    def is_admin_level(self):
        return self.role.level >= 80

    @property
    def is_manager_level(self):
        return self.role.level >= 40

    @property
    def dept_slug(self):
        return self.department.slug if self.department else None

    @property
    def employment_label(self):
        from .constants import EMPLOYMENT_TYPES
        return dict(EMPLOYMENT_TYPES).get(self.employment_type, self.employment_type)

    @property
    def title_label(self):
        """What to show as this person's role/title: their own override if they have one, else their role name."""
        return self.display_title or self.role.name

    @property
    def led_department_ids(self):
        """Departments this person leads at Team Lead level or above: their main one plus any secondary leadership role."""
        if not self.is_manager_level:
            return set()
        ids = {r.department_id for r in self.secondary_roles if r.role.level >= 40}
        if self.department_id:
            ids.add(self.department_id)
        return ids

    def works_in(self, department_id):
        """True if this is the person's main department, or one of their secondary affiliations."""
        return self.department_id == department_id or any(r.department_id == department_id for r in self.secondary_roles)

    def permission_codes(self):
        cache = g.setdefault("_perm_cache", {})
        if self.id not in cache:
            codes = {p.code for p in self.role.permissions}
            for o in self.permission_overrides:          # individual exceptions set by an admin or head
                if o.allow:
                    codes.add(o.permission.code)
                else:
                    codes.discard(o.permission.code)
            cache[self.id] = codes
        return cache[self.id]

    def manages_department(self, department_id) -> bool:
        """Admins manage every department; heads and team leads manage their own."""
        if self.is_admin_level:
            return True
        if department_id is None:
            return False
        if self.is_manager_level and self.department_id == department_id:
            return True
        return self.is_manager_level and any(r.department_id == department_id and r.role.level >= 40 for r in self.secondary_roles)


class Startup(db.Model):
    """A startup application to Home Mate's NIC Hyderabad incubation program."""
    __tablename__ = "startups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)          # startup / company name
    founder_name = db.Column(db.String(160))
    company = db.Column(db.String(160))                       # legal entity name, if different from the brand
    phone = db.Column(db.String(30))
    email = db.Column(db.String(160))
    sector = db.Column(db.String(60))
    cohort = db.Column(db.String(60))
    stage = db.Column(db.String(20), default="applied", nullable=False, index=True)
    value = db.Column(db.Float, default=0)                     # investment or grant amount under discussion, if any
    next_follow_up = db.Column(db.Date)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)   # assigned mentor / program manager
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False, index=True)

    owner = db.relationship("User")
    department = db.relationship("Department")


class DocumentType(db.Model):
    """An admin-configured category of employee document, e.g. CNIC, Degree, Contract."""
    __tablename__ = "document_types"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    category = db.Column(db.String(60), default="Other", nullable=False)
    required = db.Column(db.Boolean, default=False, nullable=False)
    expiry_required = db.Column(db.Boolean, default=False, nullable=False)
    verification_required = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class EmployeeDocument(db.Model):
    """A file an employee (or HR/admin) has uploaded against a document type."""
    __tablename__ = "employee_documents"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    expiry_date = db.Column(db.Date)
    verified = db.Column(db.Boolean, default=False, nullable=False)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_at = db.Column(db.DateTime)
    notes = db.Column(db.String(300))
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=now, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])
    document_type = db.relationship("DocumentType")
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < today())

    @property
    def expiring_soon(self):
        return bool(self.expiry_date and not self.is_expired and (self.expiry_date - today()).days <= 30)


# ------------------------------------------------------------------ platform
class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(40), nullable=False, index=True)
    entity_type = db.Column(db.String(40), index=True)
    entity_id = db.Column(db.Integer)
    description = db.Column(db.String(400))
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    user = db.relationship("User")


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.String(400))
    url = db.Column(db.String(300))
    kind = db.Column(db.String(30), default="info")
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    important = db.Column(db.Boolean, default=False, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))   # empty = whole company
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    author = db.relationship("User")
    department = db.relationship("Department")


class Comment(db.Model):
    """One comment/notes table shared by tasks, requests, tickets, leads and vendors."""
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(30), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(20), default="comment", nullable=False)   # comment | status | submission
    internal = db.Column(db.Boolean, default=False, nullable=False)      # staff-only note on tickets
    attachment_file = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    version = db.Column(db.Integer)                                      # set on kind="submission": 1, 2, 3, ...
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    author = db.relationship("User")
    __table_args__ = (db.Index("ix_comment_entity", "entity_type", "entity_id"),)


# ------------------------------------------------------------------ projects
class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    budget = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(20), default="planning", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)

    department = db.relationship("Department")
    manager = db.relationship("User")
    tasks = db.relationship("Task", back_populates="project", order_by="Task.due_date")
    member_rows = db.relationship("ProjectMember", backref="project", cascade="all, delete-orphan")

    @property
    def members(self):
        return [m.user for m in self.member_rows]

    @property
    def progress(self):
        total = len(self.tasks)
        if not total:
            return None
        done = sum(1 for t in self.tasks if t.status == "completed")
        return round(100 * done / total)

    @property
    def is_overdue(self):
        return bool(self.end_date and self.end_date < today() and self.status in ("planning", "active", "on_hold"))


class ProjectMember(db.Model):
    __tablename__ = "project_members"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("project_id", "user_id", name="uq_project_member"),)


class Campaign(db.Model):
    """A marketing campaign: objective, audience, platforms, budget, and the metrics logged against it."""
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    objective = db.Column(db.String(300))
    target_audience = db.Column(db.Text)
    platforms = db.Column(db.String(300))              # comma-separated, see platform_list
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    budget = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default="planning", nullable=False, index=True)
    # performance, logged by hand (no ad-platform integration in this version)
    reach = db.Column(db.Integer, default=0)
    leads_generated = db.Column(db.Integer, default=0)
    qualified_leads = db.Column(db.Integer, default=0)
    conversions = db.Column(db.Integer, default=0)
    engagement = db.Column(db.Integer, default=0)
    spend = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)

    department = db.relationship("Department")
    owner = db.relationship("User")
    tasks = db.relationship("Task", back_populates="campaign")

    @property
    def platform_list(self):
        return [p.strip() for p in (self.platforms or "").split(",") if p.strip()]

    @property
    def cost_per_lead(self):
        return round(self.spend / self.leads_generated, 2) if self.leads_generated else None

    @property
    def conversion_rate(self):
        return round(100 * self.conversions / self.leads_generated, 1) if self.leads_generated else None

    @property
    def is_overdue(self):
        return bool(self.end_date and self.end_date < today() and self.status in CAMPAIGN_OPEN_STATUSES)


# ------------------------------------------------------------------ work
class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    priority = db.Column(db.String(20), default="medium", nullable=False)
    status = db.Column(db.String(20), default="todo", nullable=False, index=True)
    due_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    completed_at = db.Column(db.DateTime)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), index=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    department = db.relationship("Department")
    project = db.relationship("Project", back_populates="tasks")
    campaign = db.relationship("Campaign", back_populates="tasks")
    checklist = db.relationship("TaskChecklistItem", backref="task", cascade="all, delete-orphan",
                                order_by="TaskChecklistItem.position")

    @property
    def owner_id(self):
        return self.created_by_id

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < today() and self.status in TASK_OPEN_STATUSES)

    @property
    def checklist_progress(self):
        items = self.checklist
        return (sum(1 for i in items if i.done), len(items)) if items else (0, 0)


class TaskChecklistItem(db.Model):
    __tablename__ = "task_checklist_items"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False, index=True)
    text = db.Column(db.String(300), nullable=False)
    done = db.Column(db.Boolean, default=False, nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class ContentRequest(db.Model):
    """Design & content requests to the Marketing (content and creation) team."""
    __tablename__ = "content_requests"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(60), nullable=False)
    brief = db.Column(db.Text)
    reference_link = db.Column(db.String(300))
    priority = db.Column(db.String(20), default="medium", nullable=False)
    status = db.Column(db.String(20), default="submitted", nullable=False, index=True)
    due_date = db.Column(db.Date)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    requesting_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))    # team doing the work
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    delivered_at = db.Column(db.DateTime)

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    requesting_department = db.relationship("Department", foreign_keys=[requesting_department_id])
    department = db.relationship("Department", foreign_keys=[department_id])

    @property
    def owner_id(self):
        return self.requested_by_id

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < today() and self.status in ("submitted", "in_progress", "in_review", "revision"))


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False, index=True)
    check_in = db.Column(db.DateTime)
    check_out = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="present", nullable=False)   # present | late
    note = db.Column(db.String(200))
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("user_id", "work_date", name="uq_attendance_user_day"),)

    @property
    def hours(self):
        if self.check_in and self.check_out:
            return round((self.check_out - self.check_in).total_seconds() / 3600, 2)
        return None


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    leave_type = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days = db.Column(db.Float, nullable=False, default=1)
    reason = db.Column(db.String(400))
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship("User", foreign_keys=[user_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])


# ------------------------------------------------------------------ business
class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    company = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    source = db.Column(db.String(60))
    stage = db.Column(db.String(20), default="new", nullable=False, index=True)
    value = db.Column(db.Float, default=0)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), index=True)   # the campaign that brought this lead in
    next_follow_up = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    owner = db.relationship("User")
    department = db.relationship("Department")
    campaign = db.relationship("Campaign")


class Candidate(db.Model):
    __tablename__ = "candidates"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    position = db.Column(db.String(160), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    email = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    source = db.Column(db.String(60))
    stage = db.Column(db.String(20), default="applied", nullable=False, index=True)
    expected_salary = db.Column(db.Float)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    owner = db.relationship("User")
    department = db.relationship("Department")


class Ticket(db.Model):
    """CSR helpdesk tickets, IT helpdesk tickets and general queries share one table."""
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, index=True)           # csr | it | query
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(60))
    priority = db.Column(db.String(20), default="medium", nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    customer_name = db.Column(db.String(160))          # CSR tickets
    customer_contact = db.Column(db.String(160))
    target_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))   # queries
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))               # queries to one person
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    resolved_at = db.Column(db.DateTime)

    requester = db.relationship("User", foreign_keys=[requester_id])
    target_user = db.relationship("User", foreign_keys=[target_user_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    target_department = db.relationship("Department", foreign_keys=[target_department_id])

    @property
    def number(self):
        from .constants import TICKET_PREFIX
        return f"{TICKET_PREFIX.get(self.kind, 'T')}-{self.id:04d}"


class Vendor(db.Model):
    __tablename__ = "vendors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80))
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    address = db.Column(db.String(300))
    payment_terms = db.Column(db.String(120))
    rating = db.Column(db.Integer)                      # 1-5
    status = db.Column(db.String(20), default="active", nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class Expense(db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    expense_date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.Text)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"))
    status = db.Column(db.String(20), default="submitted", nullable=False, index=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(300))
    paid_on = db.Column(db.Date)
    receipt_file = db.Column(db.String(260))
    receipt_name = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])
    department = db.relationship("Department")
    vendor = db.relationship("Vendor")


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(400))
    category = db.Column(db.String(60))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))   # empty = company-wide
    file_name = db.Column(db.String(260), nullable=False)                    # stored name on disk
    original_name = db.Column(db.String(200), nullable=False)
    size = db.Column(db.Integer, default=0)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    uploaded_by = db.relationship("User")
    department = db.relationship("Department")


# ---------------------------------------------------------------- internal messaging
class Conversation(db.Model):
    """A chat thread: either between two people (direct) or a whole department's team chat."""
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(12), nullable=False)                      # direct | department
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), unique=True)   # one team chat per department
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, nullable=False, index=True)

    department = db.relationship("Department")
    members = db.relationship("ConversationMember", backref="conversation", cascade="all, delete-orphan")
    messages = db.relationship("Message", backref="conversation", cascade="all, delete-orphan", order_by="Message.id")


class ConversationMember(db.Model):
    """Who is in a conversation, and how far they have read (drives the unread badge)."""
    __tablename__ = "conversation_members"
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    last_read_at = db.Column(db.DateTime)
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_member"),)


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    attachment_file = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    sender = db.relationship("User")


# ---------------------------------------------------------------- department-to-department requests
class InternalRequest(db.Model):
    """A request one department sends to another (Sales to Finance, Marketing to Design, ...)."""
    __tablename__ = "internal_requests"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(60))
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default="medium", nullable=False)
    status = db.Column(db.String(20), default="submitted", nullable=False, index=True)
    due_date = db.Column(db.Date)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    from_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    to_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    attachment_file = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    completed_at = db.Column(db.DateTime)

    requester = db.relationship("User", foreign_keys=[requester_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    from_department = db.relationship("Department", foreign_keys=[from_department_id])
    to_department = db.relationship("Department", foreign_keys=[to_department_id])

    # names the shared workflow engine expects
    @property
    def owner_id(self):
        return self.requester_id

    @property
    def department_id(self):
        return self.to_department_id

    @property
    def number(self):
        return f"REQ-{self.id:04d}"

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < today() and self.status in ("submitted", "assigned", "in_progress", "in_review", "revision"))


# ---------------------------------------------------------------- 1.5: vendor product lists
class VendorProduct(db.Model):
    """One item on a vendor's list: a food-menu dish, a stationery item, a service with a price, and so on."""
    __tablename__ = "vendor_products"
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    section = db.Column(db.String(80))              # menu section / product group, e.g. "Main course", "Drinks"
    sku = db.Column(db.String(60))                  # vendor's own item code, if they have one
    description = db.Column(db.String(400))
    unit = db.Column(db.String(40), default="each")
    price = db.Column(db.Float, default=0, nullable=False)
    available = db.Column(db.Boolean, default=True, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)

    vendor = db.relationship("Vendor", backref=db.backref("products", cascade="all, delete-orphan",
                                                          order_by="VendorProduct.section, VendorProduct.name"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    price_history = db.relationship("VendorProductPrice", backref="product", cascade="all, delete-orphan",
                                    order_by="VendorProductPrice.changed_at.desc()")


class VendorProductPrice(db.Model):
    """Every price change of a vendor product, so you can see how a price moved over time."""
    __tablename__ = "vendor_product_prices"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("vendor_products.id"), nullable=False, index=True)
    old_price = db.Column(db.Float)                 # empty on the first entry
    new_price = db.Column(db.Float, nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    changed_at = db.Column(db.DateTime, default=now, nullable=False)
    note = db.Column(db.String(200))
    changed_by = db.relationship("User")


# ---------------------------------------------------------------- 1.5: the Files area (any file type)
class StoredFile(db.Model):
    """A file kept in the Files area (or attached to a vendor). The bytes live on disk under instance/uploads in
    departments/<department>/<person>/<year>/<month>/..., this row remembers who owns it and who may see it."""
    __tablename__ = "stored_files"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(400))
    folder = db.Column(db.String(80), default="General", nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(300), nullable=False)          # path under instance/uploads
    size = db.Column(db.Integer, default=0)
    visibility = db.Column(db.String(12), default="private", nullable=False)   # private | department | company
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    entity_type = db.Column(db.String(30), index=True)             # e.g. "vendor" when attached to a record
    entity_id = db.Column(db.Integer, index=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)

    owner = db.relationship("User")
    department = db.relationship("Department")

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].lower() if "." in self.original_name else ""


# ---------------------------------------------------------------- 1.5: removing people from the team
class RemovalRecord(db.Model):
    """Kept forever: who was removed from the team, by whom, why, and where their open work went."""
    __tablename__ = "removal_records"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    removed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    role_name = db.Column(db.String(80))
    reason = db.Column(db.String(80))
    note = db.Column(db.String(400))
    handover_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    items_moved = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)

    user = db.relationship("User", foreign_keys=[user_id])
    removed_by = db.relationship("User", foreign_keys=[removed_by_id])
    handover_to = db.relationship("User", foreign_keys=[handover_to_id])
    department = db.relationship("Department")
