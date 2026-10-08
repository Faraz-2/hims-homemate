from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..constants import VENDOR_CATEGORIES, VENDOR_STATUSES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Expense, StoredFile, Vendor, VendorProduct
from ..security import access, has_perm
from ..services import add_comment, comments_for, log_activity, paginate

bp = Blueprint("vendors", __name__, url_prefix="/vendors")


def _form():
    return Form([
        Field("name", "Vendor name", required=True),
        Field("category", "Category", "select", options=strings(VENDOR_CATEGORIES)),
        Field("contact_person", "Contact person"),
        Field("phone", "Phone", "tel"),
        Field("email", "Email", "email"),
        Field("payment_terms", "Payment terms", placeholder="e.g. Net 30"),
        Field("address", "Address", half=False),
        Field("status", "Status", "select", required=True, options=VENDOR_STATUSES, value="active"),
        Field("rating", "Rating (1-5)", "select", coerce=int, options=[(i, f"{i} of 5") for i in range(1, 6)], blank_label="Not rated"),
        Field("notes", "Notes", "textarea", half=False, rows=3),
    ])


@bp.route("/")
@access("vendors.view", "vendors")
def index():
    text = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    status = request.args.get("status", "")
    q = Vendor.query
    if text:
        like = f"%{text}%"
        q = q.filter(or_(Vendor.name.ilike(like), Vendor.contact_person.ilike(like), Vendor.email.ilike(like)))
    if category:
        q = q.filter(Vendor.category == category)
    if status:
        q = q.filter(Vendor.status == status)
    pager = paginate(q.order_by(Vendor.name), 20)
    spend = dict(db.session.query(Expense.vendor_id, func.sum(Expense.amount))
                 .filter(Expense.vendor_id.isnot(None), Expense.status.in_(("approved", "paid"))).group_by(Expense.vendor_id).all())
    counts = dict(db.session.query(VendorProduct.vendor_id, func.count(VendorProduct.id)).group_by(VendorProduct.vendor_id).all())
    return render_template("vendors/index.html", pager=pager, q=text, category=category, status=status, spend=spend, counts=counts,
                           categories=VENDOR_CATEGORIES, statuses=VENDOR_STATUSES, can_manage=has_perm(current_user, "vendors.manage"))


@bp.route("/new", methods=["GET", "POST"])
@access("vendors.manage", "vendors")
def new():
    form = _form()
    if request.method == "POST" and form.validate(request.form):
        vendor = Vendor(**form.data)
        db.session.add(vendor)
        db.session.flush()
        log_activity("created", "vendor", vendor.id, f"Added vendor {vendor.name}")
        db.session.commit()
        flash("Vendor added.", "success")
        return redirect(url_for("vendors.detail", vid=vendor.id))
    return render_template("form_page.html", form=form, title="Add vendor", subtitle="Keep supplier details in one place.",
                           submit="Add vendor", cancel=url_for("vendors.index"))


@bp.route("/<int:vid>")
@access("vendors.view", "vendors")
def detail(vid):
    vendor = Vendor.query.get_or_404(vid)
    expenses = Expense.query.filter_by(vendor_id=vendor.id).order_by(Expense.expense_date.desc()).limit(10).all()
    total = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.vendor_id == vendor.id, Expense.status.in_(("approved", "paid"))).scalar()
    files = StoredFile.query.filter_by(entity_type="vendor", entity_id=vendor.id).order_by(StoredFile.created_at.desc()).all()
    return render_template("vendors/detail.html", vendor=vendor, expenses=expenses, total=total,
                           comments=comments_for("vendor", vendor.id), can_manage=has_perm(current_user, "vendors.manage"),
                           products=vendor.products[:8], product_count=len(vendor.products),
                           products_available=sum(1 for p in vendor.products if p.available), files=files,
                           can_products=has_perm(current_user, "vendors.products"), can_delete=has_perm(current_user, "vendors.delete"))


@bp.route("/<int:vid>/edit", methods=["GET", "POST"])
@access("vendors.manage", "vendors")
def edit(vid):
    vendor = Vendor.query.get_or_404(vid)
    form = _form().fill(vendor)
    if request.method == "POST" and form.validate(request.form):
        for key, value in form.data.items():
            setattr(vendor, key, value)
        log_activity("updated", "vendor", vendor.id, f"Updated vendor {vendor.name}")
        db.session.commit()
        flash("Vendor saved.", "success")
        return redirect(url_for("vendors.detail", vid=vendor.id))
    return render_template("form_page.html", form=form, title="Edit vendor", subtitle=vendor.name, submit="Save changes",
                           cancel=url_for("vendors.detail", vid=vendor.id))


@bp.route("/<int:vid>/comment", methods=["POST"])
@access("vendors.view", "vendors")
def comment(vid):
    vendor = Vendor.query.get_or_404(vid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("vendor", vendor.id, body)
        db.session.commit()
    return redirect(url_for("vendors.detail", vid=vendor.id) + "#comments")
