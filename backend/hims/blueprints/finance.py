from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..constants import EXPENSE_CATEGORIES, EXPENSE_STATUSES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Department, Expense, User, Vendor
from ..scopes import visible_expenses
from ..security import access, dept_ok, has_perm
from ..services import (add_comment, comments_for, delete_upload, log_activity, notify, paginate, save_upload,
                        upload_limit_text, upload_path)
from ..timeutil import now, today
from ..ui import label

bp = Blueprint("finance", __name__, url_prefix="/finance")


def _approver(user, exp=None):
    if not has_perm(user, "finance.approve") or not dept_ok(user, "finance"):
        return False
    return exp is None or exp.submitted_by_id != user.id


def _finance_users():
    return [u for u in User.query.filter_by(active=True).all() if has_perm(u, "finance.approve") and dept_ok(u, "finance")]


@bp.route("/")
@access("finance.submit")
def index():
    finance_staff = dept_ok(current_user, "finance")
    box = request.args.get("box", "all" if (finance_staff or current_user.role.level >= 60) else "mine")
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    dept = request.args.get("dept", type=int)
    text = request.args.get("q", "").strip()
    q = visible_expenses(current_user)
    if box == "mine":
        q = q.filter(Expense.submitted_by_id == current_user.id)
    if status:
        q = q.filter(Expense.status == status)
    if category:
        q = q.filter(Expense.category == category)
    if dept:
        q = q.filter(Expense.department_id == dept)
    if text:
        q = q.filter(or_(Expense.title.ilike(f"%{text}%"), Expense.description.ilike(f"%{text}%")))
    pager = paginate(q.order_by(Expense.expense_date.desc(), Expense.id.desc()), 20)

    base = visible_expenses(current_user)
    if box == "mine":
        base = base.filter(Expense.submitted_by_id == current_user.id)
    month_start = today().replace(day=1)
    def total(*filters):
        return base.with_entities(func.coalesce(func.sum(Expense.amount), 0)).filter(*filters).scalar() or 0
    summary = {
        "pending": total(Expense.status == "submitted"),
        "pending_count": base.filter(Expense.status == "submitted").count(),
        "unpaid": total(Expense.status == "approved"),
        "month": total(Expense.expense_date >= month_start, Expense.status.in_(("approved", "paid"))),
        "paid_month": total(Expense.status == "paid", Expense.expense_date >= month_start),
    }
    return render_template("finance/index.html", pager=pager, box=box, status=status, category=category, dept=dept, q=text,
                           summary=summary, statuses=EXPENSE_STATUSES, categories=EXPENSE_CATEGORIES,
                           departments=Department.query.order_by(Department.name).all(),
                           show_all=finance_staff or current_user.role.level >= 60)


@bp.route("/new", methods=["GET", "POST"])
@access("finance.submit")
def new():
    fields = [
        Field("title", "What was it for?", required=True, half=False),
        Field("category", "Category", "select", required=True, options=strings(EXPENSE_CATEGORIES)),
        Field("amount", "Amount", "number", required=True, min=1),
        Field("expense_date", "Date of expense", "date", required=True, value=today().isoformat()),
    ]
    if dept_ok(current_user, "vendors"):
        vendors = Vendor.query.filter_by(status="active").order_by(Vendor.name).all()
        fields.append(Field("vendor_id", "Vendor (if any)", "select", coerce=int, blank_label="No vendor",
                            options=[(v.id, v.name) for v in vendors]))
    fields += [Field("description", "Notes", "textarea", half=False, rows=3),
               Field("receipt", "Receipt or invoice", "file", half=False, help=f"PDF, image or Office file, up to {upload_limit_text()}.")]
    form = Form(fields)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        stored = original = None
        upload = request.files.get("receipt")
        try:
            if upload and upload.filename:
                stored, original, _ = save_upload(upload, "receipts")
        except ValueError as exc:
            form.fields[-1].error = str(exc)
        else:
            exp = Expense(title=d["title"], category=d["category"], amount=d["amount"], expense_date=d["expense_date"],
                          description=d["description"], vendor_id=d.get("vendor_id"),
                          submitted_by_id=current_user.id, department_id=current_user.department_id,
                          receipt_file=stored, receipt_name=original)
            db.session.add(exp)
            db.session.flush()
            log_activity("created", "expense", exp.id, f'Submitted expense "{exp.title}" ({exp.amount:,.0f})')
            notify(_finance_users(), "Expense awaiting approval", f'{current_user.name}: "{exp.title}" ({exp.amount:,.0f})',
                   url_for("finance.detail", eid=exp.id), kind="approval")
            db.session.commit()
            flash("Expense submitted for approval.", "success")
            return redirect(url_for("finance.detail", eid=exp.id))
    return render_template("form_page.html", form=form, title="Submit an expense",
                           subtitle="Finance will review it and you will be notified of the decision.",
                           submit="Submit expense", cancel=url_for("finance.index"))


def _get(eid):
    return visible_expenses(current_user).filter(Expense.id == eid).first_or_404()


@bp.route("/<int:eid>")
@access("finance.submit")
def detail(eid):
    exp = _get(eid)
    return render_template("finance/detail.html", exp=exp, comments=comments_for("expense", exp.id),
                           can_decide=_approver(current_user, exp), can_withdraw=exp.submitted_by_id == current_user.id and exp.status == "submitted")


@bp.route("/<int:eid>/decide", methods=["POST"])
@access("finance.approve")
def decide(eid):
    exp = _get(eid)
    if not _approver(current_user, exp):
        abort(403)
    action = request.form.get("action")
    note = request.form.get("note", "").strip()[:300]
    link = url_for("finance.detail", eid=exp.id)
    if action in ("approve", "reject") and exp.status == "submitted":
        if action == "reject" and not note:
            flash("Add a short reason so the employee knows why it was rejected.", "error")
            return redirect(link)
        exp.status = "approved" if action == "approve" else "rejected"
        exp.decided_by_id, exp.decided_at, exp.decision_note = current_user.id, now(), note or None
        add_comment("expense", exp.id, f"Expense {exp.status}." + (f" {note}" if note else ""), kind="status")
        notify(exp.submitted_by_id, f"Expense {exp.status}", f'"{exp.title}" was {exp.status}.' + (f" {note}" if note else ""), link, kind="approval")
    elif action == "pay" and exp.status == "approved":
        exp.status, exp.paid_on = "paid", today()
        add_comment("expense", exp.id, "Marked as paid.", kind="status")
        notify(exp.submitted_by_id, "Expense paid", f'"{exp.title}" has been paid.', link, kind="approval")
    else:
        abort(400)
    log_activity(exp.status, "expense", exp.id, f'Expense "{exp.title}" → {label(exp.status)}')
    db.session.commit()
    flash(f"Expense {label(exp.status).lower()}.", "success")
    return redirect(link)


@bp.route("/<int:eid>/comment", methods=["POST"])
@access("finance.submit")
def comment(eid):
    exp = _get(eid)
    body = request.form.get("body", "").strip()
    if body:
        add_comment("expense", exp.id, body)
        notify([exp.submitted_by_id] + [u.id for u in _finance_users()], "Comment on an expense", f'{current_user.name} on "{exp.title}": {body[:120]}',
               url_for("finance.detail", eid=exp.id), kind="comment")
        db.session.commit()
    return redirect(url_for("finance.detail", eid=exp.id) + "#comments")


@bp.route("/<int:eid>/withdraw", methods=["POST"])
@access("finance.submit")
def withdraw(eid):
    exp = _get(eid)
    if exp.submitted_by_id != current_user.id or exp.status != "submitted":
        abort(403)
    delete_upload(exp.receipt_file) if exp.receipt_file else None
    from ..models import Comment
    Comment.query.filter_by(entity_type="expense", entity_id=exp.id).delete()
    log_activity("deleted", "expense", exp.id, f'Withdrew expense "{exp.title}"')
    db.session.delete(exp)
    db.session.commit()
    flash("Expense withdrawn.", "success")
    return redirect(url_for("finance.index"))


@bp.route("/<int:eid>/receipt")
@access("finance.submit")
def receipt(eid):
    exp = _get(eid)
    if not exp.receipt_file:
        abort(404)
    try:
        path = upload_path(exp.receipt_file)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=exp.receipt_name or path.name)
