"""First-run defaults (idempotent) and optional demo data."""
import random
from datetime import datetime, timedelta

from flask import current_app

from .constants import DEFAULT_SETTINGS, DEPARTMENTS, DOCUMENT_TYPE_DEFAULTS, PERMISSIONS, ROLES
from .extensions import db
from .models import (Announcement, Attendance, Candidate, Comment, ContentRequest, Department, DocumentType, Expense,
                     LeaveRequest, Lead, Notification, Permission, Role, Setting, Task, Ticket, User, UserDepartmentRole,
                     Vendor, ActivityLog)
from .services import assign_employee_code
from .timeutil import now, today

DEMO_PASSWORD = "Welcome@123"

# Real Home Mate leadership, created (if missing) every time HIMS starts. They start with the same password as the
# main admin account (HIMS_ADMIN_PASSWORD) and must change it at first sign-in. Departments not listed here have no
# head, co-head or team yet: add them through Employees, or promote someone up the career ladder from their profile.
LEADERSHIP = [
    # name, email, main department slug (None = company-wide), main role, job title, display title, secondary roles
    dict(name="Faraz Haider", email="faraz.haider@homemate.local", dept=None, role="super_admin",
         job_title="Super Admin", display_title=None,
         secondary=[("marketing", "intern"), ("it", "team_lead"), ("content", "intern")]),
    dict(name="Sheraz Syed", email="sheraz.syed@homemate.local", dept=None, role="super_admin",
         job_title="Founder, Home Mate", display_title="Founder", secondary=[]),
    dict(name="Aisha", email="aisha@homemate.local", dept="hr", role="department_head",
         job_title="HR Head", display_title=None, secondary=[]),
    dict(name="Ramish", email="ramish@homemate.local", dept="it", role="department_head",
         job_title="IT Head", display_title=None, secondary=[]),
    dict(name="Hamza Khan", email="hamza.khan@homemate.local", dept="operations", role="department_head",
         job_title="Operations Head", display_title=None, secondary=[("sales", "co_head")]),
    dict(name="Warisha", email="warisha@homemate.local", dept="operations", role="co_head",
         job_title="Operations Co-Head", display_title=None, secondary=[]),
    dict(name="Maryum Batool", email="maryum.batool@homemate.local", dept="project-management", role="department_head",
         job_title="Project Management Head", display_title=None, secondary=[]),
]


def seed_leadership(roles, depts):
    """Create the named leadership accounts if they don't exist yet. Never changes anyone who already exists."""
    for person in LEADERSHIP:
        if User.query.filter(db.func.lower(User.email) == person["email"]).first():
            continue
        user = User(name=person["name"], email=person["email"], role_id=roles[person["role"]].id,
                    department_id=depts[person["dept"]].id if person["dept"] else None,
                    job_title=person["job_title"], display_title=person["display_title"],
                    employment_type="full_time", must_change_password=True, joined_on=today())
        user.set_password(current_app.config["DEFAULT_ADMIN_PASSWORD"])
        db.session.add(user)
        db.session.flush()
        assign_employee_code(user)
        for slug, role_key in person["secondary"]:
            db.session.add(UserDepartmentRole(user_id=user.id, department_id=depts[slug].id, role_id=roles[role_key].id))


def apply_default_permissions(role):
    """Give a role every permission whose minimum tier is at or below the role's own level."""
    wanted = {code for code, _label, _module, min_level in PERMISSIONS if role.level >= min_level}
    have = {p.code for p in role.permissions}
    for perm in Permission.query.filter(Permission.code.in_(wanted)).all():
        if perm.code not in have:
            role.permissions.append(perm)


def seed_defaults():
    """Create missing roles, permissions, departments, settings and the first Super Admin.

    Safe to run on every start: nothing that already exists is overwritten, so permission
    changes made in Admin > Roles & permissions are preserved.
    """
    roles = {r.key: r for r in Role.query.all()}
    created = []
    for key, name, level, description in ROLES:
        if key not in roles:
            roles[key] = Role(key=key, name=name, level=level, description=description)
            db.session.add(roles[key])
            created.append(roles[key])
    db.session.flush()

    existing = {p.code for p in Permission.query.all()}
    for code, label, module, min_level in PERMISSIONS:
        if code in existing:
            continue
        perm = Permission(code=code, label=label, module=module)
        db.session.add(perm)
        for role in roles.values():
            if role.level >= min_level:
                role.permissions.append(perm)

    if created:                       # e.g. the Intern role added by an upgrade: give it what its tier allows
        db.session.flush()
        for role in created:
            apply_default_permissions(role)

    have_depts = {d.slug for d in Department.query.all()}
    for d in DEPARTMENTS:
        if d["slug"] not in have_depts:
            db.session.add(Department(**d))

    have_settings = {s.key for s in Setting.query.all()}
    for key, value in DEFAULT_SETTINGS.items():
        if key not in have_settings:
            db.session.add(Setting(key=key, value=value))

    have_doc_types = {d.name for d in DocumentType.query.all()}
    for name, category, required, expiry_required, verification_required in DOCUMENT_TYPE_DEFAULTS:
        if name not in have_doc_types:
            db.session.add(DocumentType(name=name, category=category, required=required,
                                        expiry_required=expiry_required, verification_required=verification_required))

    if User.query.count() == 0:
        cfg = current_app.config
        admin = User(name="System Administrator", email=cfg["DEFAULT_ADMIN_EMAIL"].lower(),
                     role_id=roles["super_admin"].id, job_title="Super Admin",
                     must_change_password=True, joined_on=today())
        admin.set_password(cfg["DEFAULT_ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.flush()
        assign_employee_code(admin)

    db.session.flush()                                   # make the roles/departments queryable below
    seed_leadership({r.key: r for r in Role.query.all()}, {d.slug: d for d in Department.query.all()})
    db.session.commit()


# ---------------------------------------------------------------------------- demo data
PEOPLE = [
    ("Saad Qureshi", "operations", "admin", "Operations Director"),
    ("Ayesha Khan", "hr", "department_head", "HR Manager"),
    ("Laiba Anwar", "hr", "employee", "Recruiter"),
    ("Bilal Ahmed", "it", "department_head", "IT Manager"),
    ("Danish Mehmood", "it", "employee", "Support Engineer"),
    ("Sana Malik", "marketing", "department_head", "Head of Marketing"),
    ("Zara Sheikh", "marketing", "team_lead", "Content Lead"),
    ("Hamza Raza", "marketing", "employee", "Graphic Designer"),
    ("Usman Tariq", "sales", "department_head", "Sales Head"),
    ("Fatima Noor", "sales", "employee", "Business Development Executive"),
    ("Ali Hassan", "csr", "team_lead", "CSR Team Lead"),
    ("Mariam Yousuf", "csr", "employee", "Customer Service Representative"),
    ("Omar Farooq", "operations", "department_head", "Operations Manager"),
    ("Hina Aslam", "operations", "employee", "Data Analyst"),
    ("Kamran Iqbal", "finance", "department_head", "Finance Manager"),
    ("Nida Rauf", "finance", "employee", "Accounts Officer"),
    ("Rabia Saeed", "rnd", "employee", "Research Associate"),
    ("Taha Siddiqui", "digital-sales", "team_lead", "E-commerce Lead"),
]

TASK_TITLES = {
    "marketing": ["Plan October social calendar", "Refresh brand guidelines", "Write landing page copy", "Design festive banner set"],
    "sales": ["Follow up on warm leads", "Prepare corporate proposal", "Quarterly client review deck"],
    "csr": ["Clear complaint backlog", "Update customer FAQ", "Call back unresolved tickets"],
    "operations": ["Weekly MIS pack", "Clean service-completion Excel data", "Map delivery bottlenecks"],
    "finance": ["Reconcile vendor invoices", "Prepare monthly expense summary", "Update payment schedule"],
    "it": ["Set up backup drive", "Audit laptop inventory", "Roll out password policy"],
    "hr": ["Shortlist designer candidates", "Update leave policy handbook", "Plan onboarding checklist"],
    "rnd": ["Benchmark competitor services", "Prototype booking flow", "Write research summary"],
    "digital-sales": ["Optimise marketplace listings", "Review conversion funnel", "Launch weekend promo"],
}


def seed_demo():
    """Load sample data so every screen has something to show. Safe to run once on a fresh database."""
    first_demo_email = PEOPLE[0][0].lower().replace(" ", ".") + "@homemate.local"
    if User.query.filter(db.func.lower(User.email) == first_demo_email).first():
        return False               # sample data was already loaded
    rnd = random.Random(7)
    roles = {r.key: r for r in Role.query.all()}
    depts = {d.slug: d for d in Department.query.all()}

    users = []
    for name, slug, role_key, title in PEOPLE:
        email = name.lower().replace(" ", ".") + "@homemate.local"
        u = User(name=name, email=email, role_id=roles[role_key].id, department_id=depts[slug].id,
                 job_title=title, joined_on=today() - timedelta(days=rnd.randint(60, 900)),
                 phone=f"03{rnd.randint(0, 4)}{rnd.randint(0, 9)}-{rnd.randint(1000000, 9999999)}")
        u.set_password(DEMO_PASSWORD)
        db.session.add(u)
        users.append(u)
    db.session.flush()
    for u in users:
        assign_employee_code(u)
    by_dept = {}
    for u in users:
        by_dept.setdefault(u.department.slug, []).append(u)
    admin = User.query.filter_by(email="admin@homemate.local").first() or users[0]

    # tasks
    statuses = ["todo", "in_progress", "in_progress", "in_review", "revision", "completed", "completed", "completed"]
    for slug, titles in TASK_TITLES.items():
        team = by_dept.get(slug, [])
        if not team:
            continue
        boss = next((u for u in team if u.role.key in ("department_head", "team_lead")), team[0])
        for title in titles:
            assignee = rnd.choice(team)
            status = rnd.choice(statuses)
            created = now() - timedelta(days=rnd.randint(2, 40))
            t = Task(title=title, description="Demo task created by the sample data loader.",
                     department_id=depts[slug].id, created_by_id=boss.id, assignee_id=assignee.id,
                     priority=rnd.choice(["low", "medium", "medium", "high", "urgent"]), status=status,
                     due_date=today() + timedelta(days=rnd.randint(-6, 20)), created_at=created, updated_at=created)
            if status == "completed":
                t.completed_at = created + timedelta(days=rnd.randint(1, 10))
                t.updated_at = t.completed_at
            db.session.add(t)
            db.session.flush()
            db.session.add(Comment(entity_type="task", entity_id=t.id, user_id=boss.id, kind="status",
                                   body=f"Task created and assigned to {assignee.name}."))

    # attendance for the last 14 days
    from .services import is_working_day
    for offset in range(14, -1, -1):
        day = today() - timedelta(days=offset)
        if not is_working_day(day):
            continue
        for u in users:
            if offset == 0 and rnd.random() < 0.25:
                continue                       # not in yet today
            if rnd.random() < 0.06:
                continue                       # absent
            start = datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=rnd.randint(40, 100))
            late = start.hour * 60 + start.minute > 9 * 60 + 15
            rec = Attendance(user_id=u.id, work_date=day, check_in=start, status="late" if late else "present")
            if offset > 0:
                rec.check_out = datetime.combine(day, datetime.min.time()) + timedelta(hours=17, minutes=rnd.randint(30, 120))
            db.session.add(rec)

    # leave
    for u, kind, status, start_off, length in [
        (users[2], "annual", "pending", 5, 3), (users[7], "sick", "approved", -2, 2),
        (users[9], "casual", "pending", 8, 1), (users[13], "annual", "approved", 1, 2),
    ]:
        start = today() + timedelta(days=start_off)
        db.session.add(LeaveRequest(user_id=u.id, leave_type=kind, start_date=start,
                                    end_date=start + timedelta(days=length - 1), days=length,
                                    reason="Demo leave request.", status=status,
                                    decided_by_id=admin.id if status == "approved" else None,
                                    decided_at=now() if status == "approved" else None))

    # leads and candidates
    stages = ["new", "contacted", "qualified", "proposal", "negotiation", "won", "lost"]
    lead_owners = by_dept["sales"] + by_dept["digital-sales"] + by_dept["marketing"]
    for i, (name, company) in enumerate([
        ("Adeel Butt", "Bright Homes"), ("Sadia Karim", "Karim Estates"), ("Rehan Shah", "Shah Interiors"),
        ("Nashit Ali", "Metro Living"), ("Iqra Baig", "CityNest"), ("Waqas Dar", "Dar Builders"),
        ("Komal Zia", "Zia Furnishings"), ("Faisal Mir", "Mir Group"), ("Sehrish Gul", "Gul Traders"),
        ("Tariq Jamil", "Jamil & Sons"), ("Anum Riaz", "Riaz Enterprises"), ("Zain Abbas", "Abbas Realty")]):
        owner = rnd.choice(lead_owners)
        db.session.add(Lead(name=name, company=company, phone=f"0300-{rnd.randint(1000000, 9999999)}",
                            source=rnd.choice(["Referral", "Website", "Social media", "Event"]),
                            stage=stages[i % len(stages)], value=rnd.choice([50000, 120000, 250000, 800000]),
                            owner_id=owner.id, department_id=owner.department_id,
                            next_follow_up=today() + timedelta(days=rnd.randint(-2, 9)), notes="Demo lead."))
    hr_team = by_dept["hr"]
    for i, (name, pos) in enumerate([("Areeba Nasir", "Graphic Designer"), ("Junaid Khalid", "Sales Executive"),
                                     ("Mehwish Ali", "Customer Service Representative"), ("Owais Rana", "Data Analyst"),
                                     ("Hira Baloch", "Content Writer"), ("Shahzaib Lodhi", "IT Support")]):
        db.session.add(Candidate(name=name, position=pos, stage=["applied", "screening", "interview", "offer", "hired", "rejected"][i],
                                 source=rnd.choice(["LinkedIn", "Referral", "Rozee.pk"]), owner_id=hr_team[0].id,
                                 department_id=depts["marketing"].id, expected_salary=rnd.choice([80000, 110000, 150000])))

    # tickets
    csr_team, it_team = by_dept["csr"], by_dept["it"]
    for subject, cat, status in [("Customer reports late service arrival", "Complaint", "open"),
                                 ("Refund request for cancelled booking", "Billing", "in_progress"),
                                 ("Request for weekend service slot", "Service request", "waiting"),
                                 ("Positive feedback on technician", "Feedback", "resolved")]:
        db.session.add(Ticket(kind="csr", subject=subject, category=cat, status=status, priority=rnd.choice(["low", "medium", "high"]),
                              description="Demo CSR ticket.", requester_id=csr_team[-1].id, customer_name="Demo Customer",
                              customer_contact="0300-1234567", assignee_id=csr_team[0].id))
    for subject, cat, status in [("Laptop will not connect to office Wi-Fi", "Network or Wi-Fi", "open"),
                                 ("Need access to shared finance folder", "Account or password", "in_progress"),
                                 ("Printer jam on second floor", "Hardware", "resolved")]:
        requester = rnd.choice(users)
        db.session.add(Ticket(kind="it", subject=subject, category=cat, status=status, priority="medium",
                              description="Demo IT ticket.", requester_id=requester.id, assignee_id=it_team[-1].id))
    db.session.add(Ticket(kind="query", subject="Which template should we use for vendor onboarding?", category="General",
                          status="open", priority="low", description="Demo query to the Finance team.",
                          requester_id=by_dept["operations"][0].id, target_department_id=depts["finance"].id))

    # vendors, expenses
    vendors = []
    for name, cat in [("PrintWorks", "Printing & branding"), ("TechSource", "IT & hardware"),
                      ("SwiftLogistics", "Logistics"), ("OfficeMart", "Office supplies")]:
        v = Vendor(name=name, category=cat, contact_person="Sales Desk", phone="021-3456789", email=f"info@{name.lower()}.example",
                   payment_terms="Net 30", rating=rnd.randint(3, 5), status="active")
        db.session.add(v)
        vendors.append(v)
    db.session.flush()
    for i, (title, cat, amount) in enumerate([("Printed brochures", "Marketing", 45000), ("Wi-Fi router", "Software & IT", 18500),
                                              ("Courier charges", "Vendor payment", 7200), ("Stationery", "Office supplies", 9800),
                                              ("Team lunch", "Meals & hospitality", 12500), ("Client visit fuel", "Fuel", 6400)]):
        submitter = rnd.choice(users)
        status = ["submitted", "approved", "paid", "submitted", "rejected", "paid"][i]
        db.session.add(Expense(title=title, category=cat, amount=amount, expense_date=today() - timedelta(days=rnd.randint(0, 20)),
                               department_id=submitter.department_id, submitted_by_id=submitter.id,
                               vendor_id=vendors[i % 4].id, status=status,
                               decided_by_id=None if status == "submitted" else by_dept["finance"][0].id))

    # design & content requests
    designer = next(u for u in users if u.name == "Hamza Raza")
    for title, kind, status in [("Diwali offer banner", "Banner or poster", "in_progress"),
                                ("Service explainer video", "Video", "submitted"),
                                ("Instagram carousel for AC servicing", "Social media post", "in_review"),
                                ("Corporate brochure refresh", "Brochure or print", "approved")]:
        requester = rnd.choice(by_dept["sales"] + by_dept["digital-sales"] + by_dept["csr"])
        db.session.add(ContentRequest(title=title, kind=kind, brief="Demo brief.", status=status, priority="medium",
                                      requested_by_id=requester.id, requesting_department_id=requester.department_id,
                                      department_id=depts["marketing"].id,
                                      assignee_id=None if status == "submitted" else designer.id,
                                      due_date=today() + timedelta(days=rnd.randint(2, 12))))

    # announcements, notifications, activity
    db.session.add(Announcement(title="Welcome to HIMS", important=True, author_id=admin.id,
                                body="HIMS is now the single place for tasks, attendance, leave and requests. "
                                     "Please change your temporary password and check that your details are correct."))
    db.session.add(Announcement(title="Office timings this week", author_id=admin.id,
                                body="Working hours stay 9:00 AM to 6:00 PM. Please check in on arrival."))
    for u in users[:6]:
        db.session.add(Notification(user_id=u.id, title="Welcome to HIMS", body="Take a look around your dashboard.", url="/", kind="info"))
    for u in users[:5]:
        db.session.add(ActivityLog(user_id=u.id, action="login", entity_type="user", entity_id=u.id, description=f"{u.name} signed in"))
    db.session.commit()
    return True
