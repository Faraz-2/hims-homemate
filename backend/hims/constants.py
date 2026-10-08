"""Static definitions: roles, permissions, departments, workflows, labels."""
from collections import namedtuple

# ---------------------------------------------------------------- roles
# (key, name, level, description).  Higher level = more authority.
ROLES = [
    ("super_admin", "Super Admin", 100, "Full control: roles, permissions, settings, backups and every department."),
    ("admin", "Admin", 80, "Manages employees, departments and company-wide data."),
    ("department_head", "Department Head", 60, "Leads a department: approves work, leave and department spending."),
    ("co_head", "Co-Head", 50, "Shares department leadership with the Department Head."),
    ("team_lead", "Team Lead", 40, "Assigns and reviews work for a team inside a department."),
    ("employee", "Employee", 20, "Works on assigned tasks and uses the tools of their department."),
    ("intern", "Intern", 10, "Learning role: sees their own work, attendance and leave. Cannot assign work to others."),
]

# How much authority a role carries. Custom roles pick one of these tiers; rules such as "can approve leave"
# or "may assign tasks to juniors" are based on the tier, and the permission matrix fine-tunes each role.
TIERS = [
    (10, "Intern tier: own work only"),
    (20, "Employee tier: works on assigned tasks"),
    (40, "Team Lead tier: assigns and reviews team work"),
    (50, "Co-Head tier: shares department leadership"),
    (60, "Department Head tier: leads a department"),
    (80, "Admin tier: company-wide management"),
]
# The standard career ladder. Promoting/demoting moves one rung at a time. A person on a custom role
# (not in this list) has no "next rung" offered — an admin changes their role directly instead.
PROMOTION_LADDER = ["intern", "employee", "team_lead", "co_head", "department_head"]

EMPLOYMENT_TYPES = [("full_time", "Full-time"), ("part_time", "Part-time"), ("contract", "Contract"),
                    ("intern", "Intern"), ("probation", "On probation")]

# ---------------------------------------------------------------- departments (the 9 Home Mate departments)
DEPARTMENTS = [
    dict(slug="marketing", name="Marketing", icon="pen-tool",
         description="Brand, campaigns, content and creative design for every Home Mate service.",
         functions="Content & creation, Design, Social media, Campaigns"),
    dict(slug="sales", name="Sales & Business Development", icon="trending-up",
         description="Wins new customers and partners and keeps the lead pipeline moving.",
         functions="Lead follow-up, Proposals, Business development"),
    dict(slug="csr", name="CSR (Customer Service)", icon="headphones",
         description="First point of contact for customers: complaints, requests and follow-ups.",
         functions="CSR helpdesk, Complaints, Service follow-up"),
    dict(slug="operations", name="Operations", icon="bar-chart",
         description="Service delivery, Excel and data analysis, and MIS reporting for management.",
         functions="Excel & data analysis, MIS reporting, Process control"),
    dict(slug="finance", name="Finance", icon="dollar",
         description="Expenses, payments, budgets and vendor management.",
         functions="Expense tracking, Vendor management, Payments"),
    dict(slug="it", name="IT", icon="monitor",
         description="Keeps devices, network and internal systems (including HIMS) running.",
         functions="IT helpdesk, Infrastructure, HIMS support"),
    dict(slug="hr", name="HR & Recruitment", icon="users",
         description="Hiring, onboarding, attendance, leave and employee wellbeing.",
         functions="Recruitment, Onboarding, Attendance & leave"),
    dict(slug="rnd", name="Research & Development", icon="flask",
         description="Explores new services, tools and improvements for Home Mate.",
         functions="Research, Prototyping, Process improvement"),
    dict(slug="digital-sales", name="Digital Sales", icon="globe",
         description="Online sales channels, digital leads and e-commerce performance.",
         functions="Online channels, Digital leads, Conversion tracking"),
    dict(slug="nic-hyderabad", name="NIC Hyderabad", icon="rocket",
         description="Runs Home Mate's work with the National Incubation Center Hyderabad: the startup "
                     "incubation pipeline, mentor coordination, cohort tracking and reporting back to the NIC network.",
         functions="Startup applications, Mentor coordination, Cohort management, Resource booking, NIC reporting"),
    dict(slug="project-management", name="Project Management", icon="layers",
         description="Works across every department: coordinates cross-department projects, tracks deadlines and "
                     "milestones company-wide, and reports overall project health to leadership.",
         functions="Cross-department projects, Milestones & deadlines, Resourcing, Status reporting"),
    dict(slug="content", name="Content Creation", icon="edit",
         description="Plans and produces the content Marketing and Digital Sales publish: posts, copy, scripts, "
                     "captions and creative drafts, ready for campaign use.",
         functions="Content calendar, Copywriting, Scripts & captions, Content approval"),
    dict(slug="vendor-management", name="Vendor Management", icon="truck",
         description="Finds, onboards and manages every supplier Home Mate buys from: vendor records, each vendor's "
                     "product or menu list with prices, contracts, performance and payment follow-up.",
         functions="Vendor onboarding, Product & price lists, Contracts, Vendor performance, Purchasing"),
]

# Module -> department slugs whose members work in that module.
# Admin-level roles (Admin, Super Admin) always pass these gates.
GATES = {
    "leads": {"marketing", "sales", "digital-sales"},
    "recruitment": {"hr"},
    "finance": {"finance"},
    "vendors": {"finance", "operations", "vendor-management"},
    "creative": {"marketing", "content"},
    "csr": {"csr"},
    "it": {"it"},
    "incubation": {"nic-hyderabad"},
    "campaigns": {"marketing", "digital-sales", "content"},
}

# ---------------------------------------------------------------- permissions
# (code, label, module, minimum role level that gets it by default)
PERMISSIONS = [
    ("dashboard.view", "View dashboard", "Dashboard", 10),
    ("messages.use", "Send and read messages", "Communication", 10),
    ("requests.create", "Send and receive department requests", "Requests", 20),
    ("requests.manage", "Assign and decide department requests", "Requests", 40),
    ("employees.view", "View employee directory", "Employees", 10),
    ("employees.manage", "Create and edit employees", "Employees", 80),
    ("employees.manage_team", "Add and manage employees in own department", "Employees", 60),
    ("departments.view", "View departments", "Departments", 10),
    ("departments.manage", "Create and edit departments", "Departments", 80),
    ("tasks.view", "View tasks", "Tasks", 10),
    ("tasks.create", "Create tasks", "Tasks", 10),
    ("tasks.assign", "Assign tasks to others", "Tasks", 40),
    ("tasks.approve", "Approve or send back tasks", "Tasks", 40),
    ("tasks.delete", "Delete tasks", "Tasks", 60),
    ("attendance.self", "Check in and out", "Attendance", 10),
    ("attendance.team", "View team attendance", "Attendance", 40),
    ("leaves.request", "Request leave", "Leave", 10),
    ("leaves.approve", "Approve or reject leave", "Leave", 40),
    ("leads.view", "View lead pipeline", "Sales & Marketing", 20),
    ("leads.manage", "Create and update leads", "Sales & Marketing", 20),
    ("leads.delete", "Delete leads", "Sales & Marketing", 60),
    ("recruitment.view", "View candidates", "Recruitment", 20),
    ("recruitment.manage", "Manage candidates", "Recruitment", 20),
    ("tickets.create", "Raise IT tickets and queries", "Helpdesk", 10),
    ("tickets.manage", "Work on helpdesk tickets", "Helpdesk", 20),
    ("finance.submit", "Submit expenses", "Finance", 20),
    ("finance.view", "View all expenses", "Finance", 20),
    ("finance.approve", "Approve and pay expenses", "Finance", 60),
    ("vendors.view", "View vendors", "Vendors", 20),
    ("vendors.manage", "Create and edit vendors", "Vendors", 40),
    ("creative.request", "Request design and content", "Design & Content", 20),
    ("creative.manage", "Work on design and content requests", "Design & Content", 20),
    ("documents.view", "View documents", "Documents", 10),
    ("documents.upload", "Upload documents", "Documents", 20),
    ("documents.manage", "Delete any document", "Documents", 60),
    ("announcements.post", "Post announcements", "Announcements", 60),
    ("reports.view", "View MIS reports", "Reports", 40),
    ("reports.export", "Export reports to CSV", "Reports", 40),
    ("activity.view", "View the activity log", "Administration", 80),
    ("admin.settings", "Change system settings", "Administration", 80),
    ("admin.roles", "Edit roles and permissions", "Administration", 100),
    ("admin.backup", "Create and download backups", "Administration", 80),
    ("projects.view", "View projects", "Projects", 20),
    ("projects.manage", "Create and manage projects", "Projects", 40),
    ("employees.documents", "View and manage employee documents and certifications", "Employees", 60),
    ("incubation.view", "View the NIC Hyderabad incubation pipeline", "NIC Hyderabad", 20),
    ("incubation.manage", "Manage startup applications and mentors", "NIC Hyderabad", 40),
    ("campaigns.view", "View marketing campaigns", "Campaigns", 20),
    ("campaigns.manage", "Create and manage campaigns, and log their performance", "Campaigns", 40),
    ("employees.promote", "Promote or demote an employee one rung on the career ladder", "Employees", 60),
    ("projects.view_all", "See every project company-wide (Project Management department)", "Projects", 20),
    # ---- added in 1.5
    ("vendors.products", "Add, edit and delete a vendor's products, menu items and prices", "Vendors", 20),
    ("vendors.delete", "Delete vendors (only when no expense is linked to them)", "Vendors", 60),
    ("files.view", "Open the Files area (own, department and company files)", "Files", 10),
    ("files.upload", "Upload files of any type", "Files", 10),
    ("files.manage", "Edit or delete any file visible to you in the Files area", "Files", 60),
    ("employees.departments", "Add or remove a person's role in another department", "Employees", 60),
    ("employees.remove", "Remove people from the team and hand their work to someone else", "Employees", 60),
]

DEFAULT_SETTINGS = {
    "company_name": "Home Mate",
    "work_start": "09:00",
    "work_end": "18:00",
    "late_grace_minutes": "15",
    "working_days": "0,1,2,3,4",      # Monday=0 ... Sunday=6
    "leave_annual": "14",
    "leave_sick": "8",
    "leave_casual": "6",
    "currency": "PKR",
    "backup_keep": "14",
    "backup_secondary_path": "",
    # 1.5: complete (department-wise, user-wise, date-wise) backups
    "auto_backup": "1",              # 1 = HIMS makes a complete backup by itself every day while it is running
    "auto_backup_time": "22:00",
    "backup_keep_runs": "10",        # how many complete backup runs (one per day or manual run) to keep
}

# ---------------------------------------------------------------- statuses
TASK_STATUSES = [("todo", "Assigned"), ("accepted", "Accepted"), ("in_progress", "In progress"), ("blocked", "Blocked"),
                 ("in_review", "In review"), ("revision", "Revision requested"), ("completed", "Completed"),
                 ("rejected", "Rejected"), ("cancelled", "Cancelled")]
TASK_OPEN = ("todo", "accepted", "in_progress", "blocked", "in_review", "revision")
PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")]

CONTENT_STATUSES = [("submitted", "Submitted"), ("in_progress", "In progress"), ("in_review", "In review"),
                    ("revision", "Revision requested"), ("approved", "Approved"), ("delivered", "Delivered"),
                    ("cancelled", "Cancelled")]
CONTENT_OPEN = ("submitted", "in_progress", "in_review", "revision", "approved")
CONTENT_KINDS = ["Social media post", "Graphic design", "Banner or poster", "Video", "Copywriting",
                 "Presentation", "Brochure or print", "Website content", "Other"]

LEAD_STAGES = [("new", "New"), ("contacted", "Contacted"), ("qualified", "Qualified"),
               ("proposal", "Proposal sent"), ("negotiation", "Negotiation"), ("won", "Won"), ("lost", "Lost")]
LEAD_SOURCES = ["Referral", "Website", "Social media", "Walk-in", "Phone call", "Event", "Marketplace", "Other"]

CANDIDATE_STAGES = [("applied", "Applied"), ("screening", "Screening"), ("interview", "Interview"),
                    ("offer", "Offer"), ("hired", "Hired"), ("rejected", "Rejected")]

TICKET_STATUSES = [("open", "Open"), ("in_progress", "In progress"), ("waiting", "Waiting for reply"),
                   ("resolved", "Resolved"), ("closed", "Closed")]
TICKET_OPEN = ("open", "in_progress", "waiting")
TICKET_KINDS = {"csr": "CSR helpdesk", "it": "IT helpdesk", "query": "Query"}
TICKET_URL_KIND = {"csr": "csr", "it": "it", "queries": "query"}   # URL segment -> stored kind
TICKET_PREFIX = {"csr": "CSR", "it": "IT", "query": "Q"}
CSR_CATEGORIES = ["Complaint", "Service request", "Follow-up", "Feedback", "Billing", "Other"]
IT_CATEGORIES = ["Hardware", "Software", "Network or Wi-Fi", "Account or password", "HIMS", "Other"]

LEAVE_TYPES = [("annual", "Annual leave"), ("sick", "Sick leave"), ("casual", "Casual leave"),
               ("unpaid", "Unpaid leave"), ("other", "Other")]
LEAVE_STATUSES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("cancelled", "Cancelled")]

EXPENSE_STATUSES = [("submitted", "Submitted"), ("approved", "Approved"), ("rejected", "Rejected"), ("paid", "Paid")]
EXPENSE_CATEGORIES = ["Office supplies", "Travel", "Fuel", "Utilities", "Marketing", "Software & IT",
                      "Vendor payment", "Meals & hospitality", "Maintenance", "Other"]

VENDOR_STATUSES = [("active", "Active"), ("on_hold", "On hold"), ("blacklisted", "Blacklisted")]
VENDOR_CATEGORIES = ["Printing & branding", "IT & hardware", "Office supplies", "Logistics", "Utilities",
                     "Marketing services", "Maintenance", "Professional services", "Food & catering", "Other"]

DOC_CATEGORIES = ["Policy", "Template", "Contract", "Report", "Brand asset", "Manual", "Other"]

ALLOWED_UPLOADS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "heic", "doc", "docx", "xls", "xlsx", "csv",
                   "ppt", "pptx", "txt", "zip", "svg",
                   "mp4", "mov", "m4v", "webm", "avi", "mkv"}      # photos, screenshots and videos as proof of work

# ---------------------------------------------------------------- workflows
Transition = namedtuple("Transition", "src dst label who note style")
# who: 'assignee' (doer), 'creator' (person who asked), 'manager' (dept lead/head/admin)
TASK_FLOW = [
    Transition(("todo",), "accepted", "Accept task", {"assignee"}, False, "primary"),
    Transition(("todo", "accepted"), "in_progress", "Start work", {"assignee", "manager"}, False, "primary"),
    Transition(("in_progress",), "blocked", "Mark as blocked", {"assignee", "manager"}, True, "secondary"),
    Transition(("blocked",), "in_progress", "Resume work", {"assignee", "manager"}, False, "primary"),
    Transition(("in_progress", "blocked"), "in_review", "Submit for review", {"assignee", "manager"}, False, "primary"),
    Transition(("in_review",), "completed", "Approve and complete", {"creator", "manager"}, False, "primary"),
    Transition(("in_review",), "revision", "Request revision", {"creator", "manager"}, True, "secondary"),
    Transition(("in_review",), "rejected", "Reject task", {"creator", "manager"}, True, "danger"),
    Transition(("revision",), "in_progress", "Resume work", {"assignee", "manager"}, False, "primary"),
    Transition(("completed", "rejected"), "in_progress", "Reopen", {"creator", "manager"}, True, "secondary"),
    Transition(("todo", "accepted", "in_progress", "blocked", "in_review", "revision"), "cancelled", "Cancel task",
               {"creator", "manager"}, False, "danger"),
    Transition(("cancelled",), "todo", "Restore", {"creator", "manager"}, False, "secondary"),
]
CONTENT_FLOW = [
    Transition(("submitted",), "in_progress", "Start work", {"assignee", "manager"}, False, "primary"),
    Transition(("in_progress",), "in_review", "Send for review", {"assignee", "manager"}, False, "primary"),
    Transition(("in_review",), "approved", "Approve", {"creator", "manager"}, False, "primary"),
    Transition(("in_review",), "revision", "Request revision", {"creator", "manager"}, True, "secondary"),
    Transition(("revision",), "in_progress", "Resume work", {"assignee", "manager"}, False, "primary"),
    Transition(("approved",), "delivered", "Mark as delivered", {"assignee", "manager"}, False, "primary"),
    Transition(("submitted", "in_progress", "revision"), "cancelled", "Cancel request",
               {"creator", "manager"}, False, "danger"),
]

# ---------------------------------------------------------------- department-to-department requests
# ---------------------------------------------------------------- projects
PROJECT_STATUSES = [("planning", "Planning"), ("active", "Active"), ("on_hold", "On hold"),
                    ("completed", "Completed"), ("cancelled", "Cancelled")]
PROJECT_OPEN = ("planning", "active", "on_hold")

# ---------------------------------------------------------------- employee documents & certifications
DOCUMENT_TYPE_DEFAULTS = [
    # (name, category, required_by_default, expiry_required_by_default, verification_required_by_default)
    ("CNIC", "Identity", False, False, True), ("Passport", "Identity", False, True, True),
    ("Educational certificate / degree", "Education", False, False, True), ("Resume / CV", "Application", False, False, False),
    ("Experience letter", "Employment history", False, False, False), ("Internship certificate", "Employment history", False, False, False),
    ("Professional certification", "Training", False, True, True), ("Training certificate", "Training", False, True, False),
    ("Offer letter", "Contract", False, False, False), ("Employment contract", "Contract", False, True, True),
    ("NDA", "Contract", False, False, False), ("Bank information", "Payroll", False, False, True),
]

# ---------------------------------------------------------------- NIC Hyderabad: startup incubation
STARTUP_STAGES = [("applied", "Applied"), ("screening", "Screening"), ("interview", "Interview"),
                  ("selected", "Selected"), ("onboarded", "Onboarded"), ("graduated", "Graduated"), ("rejected", "Rejected")]
STARTUP_SECTORS = ["Home services", "Fintech", "E-commerce", "HealthTech", "EdTech", "AgriTech", "Logistics",
                   "SaaS / software", "Manufacturing", "Other"]
COHORTS = ["Cohort 1", "Cohort 2", "Cohort 3", "Cohort 4", "Cohort 5", "Not yet assigned"]

# ---------------------------------------------------------------- marketing campaigns
CAMPAIGN_STATUSES = [("planning", "Planning"), ("active", "Active"), ("paused", "Paused"),
                     ("completed", "Completed"), ("cancelled", "Cancelled")]
CAMPAIGN_OPEN = ("planning", "active", "paused")
CAMPAIGN_PLATFORMS = ["Instagram", "Facebook", "TikTok", "LinkedIn", "YouTube", "WhatsApp", "Google Ads",
                      "SMS", "Email", "In-store / offline", "Other"]

REQUEST_CATEGORIES = ["General", "Design or creative", "Content", "Finance or payment", "HR", "Vendor or purchasing",
                      "Data or MIS", "Operations", "Technical", "Other"]
REQUEST_STATUSES = [("submitted", "Submitted"), ("assigned", "Assigned"), ("in_progress", "In progress"), ("in_review", "In review"),
                    ("revision", "Revision requested"), ("completed", "Completed"), ("declined", "Declined"), ("cancelled", "Cancelled")]
REQUEST_OPEN = ("submitted", "assigned", "in_progress", "in_review", "revision")
REQUEST_FLOW = [
    Transition(("assigned",), "in_progress", "Start work", {"assignee", "manager"}, False, "primary"),
    Transition(("in_progress",), "in_review", "Submit for review", {"assignee", "manager"}, False, "primary"),
    Transition(("in_review",), "completed", "Approve and complete", {"creator", "manager"}, False, "primary"),
    Transition(("in_review",), "revision", "Request revision", {"creator", "manager"}, True, "secondary"),
    Transition(("revision",), "in_progress", "Resume work", {"assignee", "manager"}, False, "primary"),
    Transition(("submitted", "assigned"), "declined", "Decline request", {"manager"}, True, "danger"),
    Transition(("submitted", "assigned", "in_progress", "revision"), "cancelled", "Cancel request", {"creator", "manager"}, False, "danger"),
]

# ---------------------------------------------------------------- department workspace tools
# slug -> [(label, endpoint, url params, icon, blurb)]
DEPT_TOOLS = {
    "marketing": [
        ("Design and content requests", "creative.index", {}, "pen-tool", "Briefs, drafts, revisions and delivery."),
        ("Campaign leads", "leads.board", {}, "trending-up", "Leads generated by campaigns."),
    ],
    "sales": [("Lead pipeline", "leads.board", {}, "trending-up", "Track every lead from first contact to won.")],
    "csr": [("CSR helpdesk", "helpdesk.index", {"kind": "csr"}, "headphones", "Customer complaints and requests.")],
    "operations": [
        ("MIS and reports", "reports.index", {}, "bar-chart", "Company-wide numbers and CSV exports."),
        ("Vendors", "vendors.index", {}, "truck", "Supplier records used by operations."),
    ],
    "finance": [
        ("Expense tracking", "finance.index", {}, "dollar", "Review, approve and pay expenses."),
        ("Vendor management", "vendors.index", {}, "truck", "Suppliers, contacts and payment terms."),
    ],
    "it": [("IT helpdesk", "helpdesk.index", {"kind": "it"}, "monitor", "Support tickets from every team.")],
    "hr": [
        ("Recruitment", "recruitment.board", {}, "user-check", "Candidates from application to hire."),
        ("Leave requests", "leaves.index", {}, "calendar", "Requests and balances."),
        ("Employee directory", "employees.index", {}, "users", "Everyone at Home Mate."),
    ],
    "rnd": [("Documents", "documents.index", {}, "folder", "Research notes and shared files.")],
    "nic-hyderabad": [
        ("Startup applications", "incubation.board", {}, "rocket", "Every application, from first contact to graduation."),
        ("Documents", "documents.index", {}, "folder", "MoUs, cohort reports and NIC paperwork."),
    ],
    "marketing": [
        ("Campaigns", "campaigns.index", {}, "megaphone", "Plan, run and track every campaign."),
        ("Design & content requests", "creative.index", {}, "pen-tool", "Brief the content and creation team."),
    ],
    "digital-sales": [
        ("Campaigns", "campaigns.index", {}, "megaphone", "The campaigns driving digital leads."),
        ("Lead pipeline", "leads.board", {}, "trending-up", "Online and marketplace leads."),
    ],
    "project-management": [
        ("All projects", "projects.index", {}, "layers", "Every project company-wide, not just your own department's."),
    ],
    "content": [
        ("Design & content requests", "creative.index", {}, "pen-tool", "Briefs from every department."),
        ("Campaigns", "campaigns.index", {}, "megaphone", "Upload content and creative to live campaigns."),
    ],
}

# ---------------------------------------------------------------- 1.5: vendor product lists, files, removing people
DEPT_TOOLS["vendor-management"] = [
    ("Vendors and product lists", "vendors.index", {}, "truck", "Every supplier, with their products, menu and prices."),
    ("Expenses", "finance.index", {}, "dollar", "Payments and expenses linked to vendors."),
    ("Files", "files.index", {}, "archive", "Contracts, price lists and vendor documents."),
]

VENDOR_UNITS = ["each", "plate", "portion", "serving", "box", "pack", "dozen", "kg", "litre", "hour", "day", "month", "job", "other"]
VENDOR_SECTIONS_HINT = "e.g. Starters, Main course, Drinks, Desserts, Stationery, Cleaning supplies"

FILE_FOLDERS = ["General", "Contracts", "Invoices", "Price lists", "Reports", "Proposals", "Designs", "Policies",
                "Photos and videos", "Personal", "Other"]
FILE_VISIBILITY = [("private", "Only me (and administrators)"), ("department", "My department"), ("company", "Whole company")]

REMOVAL_REASONS = ["Resigned", "Contract ended", "Internship ended", "Terminated", "Moved to another company",
                   "Restructuring", "Other"]
