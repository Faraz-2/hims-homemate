from flask import Blueprint, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..models import Candidate, ContentRequest, InternalRequest, Department, Document, Expense, Lead, Task, Ticket, User, Vendor
from ..scopes import (visible_content_requests, visible_documents, visible_expenses, visible_requests, visible_tasks, visible_tickets)
from ..security import dept_ok, has_perm
from ..ui import label

bp = Blueprint("search", __name__)

LIMIT = 8


def _like(q):
    return f"%{q}%"


@bp.route("/search")
@login_required
def index():
    text = request.args.get("q", "").strip()
    groups = []
    if len(text) >= 2:
        like = _like(text)
        u = current_user

        def add(title, icon, rows):
            if rows:
                groups.append({"title": title, "icon": icon, "rows": rows})

        if has_perm(u, "employees.view"):
            rows = User.query.filter(User.active.is_(True), or_(User.name.ilike(like), User.email.ilike(like), User.job_title.ilike(like),
                                                                User.employee_code.ilike(like), User.phone.ilike(like))).order_by(User.name).limit(LIMIT).all()
            add("Employees", "users", [dict(title=r.name, sub=f"{r.job_title or r.role.name} · {r.department.name if r.department else 'No department'}",
                                            url=url_for("employees.profile", uid=r.id)) for r in rows])
        if has_perm(u, "departments.view"):
            rows = Department.query.filter(Department.active.is_(True), or_(Department.name.ilike(like), Department.description.ilike(like),
                                                                             Department.functions.ilike(like))).limit(LIMIT).all()
            add("Departments", "building", [dict(title=r.name, sub=r.functions or "", url=url_for("departments.workspace", slug=r.slug))
                                            for r in rows if u.is_admin_level or u.department_id == r.id])
        if has_perm(u, "tasks.view"):
            rows = visible_tasks(u).filter(or_(Task.title.ilike(like), Task.description.ilike(like))).order_by(Task.updated_at.desc()).limit(LIMIT).all()
            add("Tasks", "check-square", [dict(title=r.title, sub=f"{label(r.status)} · {r.assignee.name if r.assignee else 'Unassigned'}",
                                               url=url_for("tasks.detail", tid=r.id)) for r in rows])
        if has_perm(u, "tickets.create"):
            rows = visible_tickets(u).filter(or_(Ticket.subject.ilike(like), Ticket.description.ilike(like), Ticket.customer_name.ilike(like))).order_by(Ticket.updated_at.desc()).limit(LIMIT).all()
            add("Tickets and queries", "life-buoy", [dict(title=f"{r.number}  {r.subject}", sub=f"{label(r.status)} · {r.requester.name}",
                                                          url=url_for("helpdesk.detail", tid=r.id)) for r in rows])
        if has_perm(u, "creative.request"):
            rows = visible_content_requests(u).filter(or_(ContentRequest.title.ilike(like), ContentRequest.brief.ilike(like))).limit(LIMIT).all()
            add("Design and content requests", "pen-tool", [dict(title=r.title, sub=f"{r.kind} · {label(r.status)}", url=url_for("creative.detail", rid=r.id)) for r in rows])
        if has_perm(u, "requests.create"):
            rows = visible_requests(u).filter(or_(InternalRequest.title.ilike(like), InternalRequest.description.ilike(like))).limit(LIMIT).all()
            add("Department requests", "arrow-right", [dict(title=f"{r.number}  {r.title}", sub=f"{r.from_department.name if r.from_department else 'Unknown'} to {r.to_department.name} · {label(r.status)}",
                                                           url=url_for("dept_requests.detail", rid=r.id)) for r in rows])
        if has_perm(u, "vendors.view") and dept_ok(u, "vendors"):
            rows = Vendor.query.filter(or_(Vendor.name.ilike(like), Vendor.contact_person.ilike(like), Vendor.email.ilike(like))).limit(LIMIT).all()
            add("Vendors", "truck", [dict(title=r.name, sub=r.category or "", url=url_for("vendors.detail", vid=r.id)) for r in rows])
        if has_perm(u, "vendors.view") and dept_ok(u, "vendors"):
            from ..models import VendorProduct
            rows = (VendorProduct.query.join(Vendor, VendorProduct.vendor_id == Vendor.id)
                    .filter(or_(VendorProduct.name.ilike(like), VendorProduct.section.ilike(like), VendorProduct.sku.ilike(like))).limit(LIMIT).all())
            add("Vendor products and menus", "list", [dict(title=r.name, sub=f"{r.vendor.name} · {r.section or 'No section'}",
                                                            url=url_for("vendor_products.index", vid=r.vendor_id)) for r in rows])
        if has_perm(u, "files.view"):
            from ..models import StoredFile
            from ..scopes import visible_files
            rows = (visible_files(u).filter(StoredFile.entity_type.is_(None))
                    .filter(or_(StoredFile.title.ilike(like), StoredFile.original_name.ilike(like), StoredFile.description.ilike(like))).limit(LIMIT).all())
            add("Files", "archive", [dict(title=r.title, sub=f"{r.folder} · {r.original_name}", url=url_for("files.download", fid=r.id)) for r in rows])
        if has_perm(u, "documents.view"):
            rows = visible_documents(u).filter(or_(Document.title.ilike(like), Document.description.ilike(like), Document.original_name.ilike(like))).limit(LIMIT).all()
            add("Documents", "folder", [dict(title=r.title, sub=r.original_name, url=url_for("documents.download", did=r.id)) for r in rows])
        if has_perm(u, "leads.view") and dept_ok(u, "leads"):
            rows = Lead.query.filter(or_(Lead.name.ilike(like), Lead.company.ilike(like), Lead.email.ilike(like), Lead.phone.ilike(like))).limit(LIMIT).all()
            add("Leads", "trending-up", [dict(title=r.name, sub=f"{r.company or ''} · {label(r.stage)}", url=url_for("leads.detail", iid=r.id)) for r in rows])
        if has_perm(u, "recruitment.view") and dept_ok(u, "recruitment"):
            rows = Candidate.query.filter(or_(Candidate.name.ilike(like), Candidate.position.ilike(like), Candidate.email.ilike(like))).limit(LIMIT).all()
            add("Candidates", "user-check", [dict(title=r.name, sub=f"{r.position} · {label(r.stage)}", url=url_for("recruitment.detail", iid=r.id)) for r in rows])
        if has_perm(u, "finance.submit"):
            rows = visible_expenses(u).filter(or_(Expense.title.ilike(like), Expense.description.ilike(like))).limit(LIMIT).all()
            add("Expenses", "dollar", [dict(title=r.title, sub=f"{r.category} · {label(r.status)}", url=url_for("finance.detail", eid=r.id)) for r in rows])
    return render_template("search/index.html", q=text, groups=groups, total=sum(len(g["rows"]) for g in groups))
