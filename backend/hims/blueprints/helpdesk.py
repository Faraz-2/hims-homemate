from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..constants import (CSR_CATEGORIES, GATES, IT_CATEGORIES, PRIORITIES, TICKET_KINDS, TICKET_OPEN,
                         TICKET_STATUSES, TICKET_URL_KIND)
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Department, Ticket, User
from ..scopes import is_ticket_staff, visible_tickets
from ..security import access, dept_ok
from ..services import add_comment, comments_for, log_activity, notify, paginate
from ..timeutil import now
from ..ui import label

bp = Blueprint("helpdesk", __name__, url_prefix="/helpdesk")

KIND_TO_URL = {v: k for k, v in TICKET_URL_KIND.items()}
TITLES = {"csr": "CSR helpdesk", "it": "IT helpdesk", "query": "Queries"}
INTRO = {
    "csr": "Customer complaints, requests and follow-ups handled by the CSR team.",
    "it": "Laptops, Wi-Fi, accounts and HIMS: raise a ticket and IT will pick it up.",
    "query": "Ask any department, or one person, a question that doesn't belong in a task.",
}


def _kind(url_kind):
    kind = TICKET_URL_KIND.get(url_kind)
    if kind is None:
        abort(404)
    if kind == "csr" and not dept_ok(current_user, "csr"):
        abort(403)
    return kind


def _staff_pool(t):
    """People a ticket can be assigned to."""
    q = User.query.filter_by(active=True)
    if t.kind == "query":
        if t.target_user_id:
            return [t.target_user] if t.target_user else []
        return q.filter_by(department_id=t.target_department_id).order_by(User.name).all()
    slugs = GATES["csr" if t.kind == "csr" else "it"]
    return (q.join(Department, User.department_id == Department.id).filter(Department.slug.in_(slugs))
            .order_by(User.name).all())


def _staff_of_kind(kind, target_department_id=None, target_user_id=None):
    if kind == "query":
        if target_user_id:
            return [target_user_id]
        return [u.id for u in User.query.filter_by(active=True, department_id=target_department_id).all()]
    slugs = GATES["csr" if kind == "csr" else "it"]
    return [u.id for u in User.query.join(Department, User.department_id == Department.id)
            .filter(User.active.is_(True), Department.slug.in_(slugs)).all()]


@bp.route("/<kind>/")
@access("tickets.create")
def index(kind):
    stored = _kind(kind)
    default_box = "all" if (stored == "csr" or current_user.is_admin_level or
                            dept_ok(current_user, "it") and stored == "it") else ("inbox" if stored == "query" else "mine")
    box = request.args.get("box", default_box)
    status = request.args.get("status", "open")
    text = request.args.get("q", "").strip()
    q = visible_tickets(current_user, stored)
    if box == "mine":
        q = q.filter(Ticket.requester_id == current_user.id)
    elif box == "assigned":
        q = q.filter(Ticket.assignee_id == current_user.id)
    elif box == "inbox":
        conds = [Ticket.target_user_id == current_user.id]
        if current_user.department_id:
            conds.append(Ticket.target_department_id == current_user.department_id)
        q = q.filter(Ticket.requester_id != current_user.id, or_(*conds))
    if status == "open":
        q = q.filter(Ticket.status.in_(TICKET_OPEN))
    elif status != "all":
        q = q.filter(Ticket.status == status)
    if text:
        like = f"%{text}%"
        q = q.filter(or_(Ticket.subject.ilike(like), Ticket.description.ilike(like), Ticket.customer_name.ilike(like)))
    pager = paginate(q.order_by(Ticket.updated_at.desc()), 20)
    base = visible_tickets(current_user, stored)
    counts = {"open": base.filter(Ticket.status.in_(TICKET_OPEN)).count(),
              "urgent": base.filter(Ticket.status.in_(TICKET_OPEN), Ticket.priority == "urgent").count(),
              "unassigned": base.filter(Ticket.status.in_(TICKET_OPEN), Ticket.assignee_id.is_(None)).count()}
    return render_template("helpdesk/index.html", pager=pager, kind=stored, url_kind=kind, title=TITLES[stored],
                           intro=INTRO[stored], box=box, status=status, q=text, counts=counts, statuses=TICKET_STATUSES)


@bp.route("/<kind>/new", methods=["GET", "POST"])
@access("tickets.create")
def new(kind):
    stored = _kind(kind)
    priority = Field("priority", "Priority", "select", required=True, options=PRIORITIES, value="medium")
    if stored == "csr":
        fields = [Field("customer_name", "Customer name", required=True), Field("customer_contact", "Customer phone or email"),
                  Field("category", "Type", "select", required=True, options=strings(CSR_CATEGORIES)),
                  priority, Field("subject", "Summary", required=True, half=False),
                  Field("description", "What happened", "textarea", half=False, rows=5)]
    elif stored == "it":
        fields = [Field("category", "Problem area", "select", required=True, options=strings(IT_CATEGORIES)), priority,
                  Field("subject", "Summary", required=True, half=False),
                  Field("description", "Details", "textarea", half=False, rows=5,
                        help="What were you doing, what did you expect, and what happened instead?")]
    else:
        depts = Department.query.filter_by(active=True).order_by(Department.name).all()
        people = User.query.filter(User.active.is_(True), User.id != current_user.id).order_by(User.name).all()
        fields = [Field("target_department_id", "Ask a department", "select", coerce=int, blank_label="—",
                        options=[(d.id, d.name) for d in depts], value=request.args.get("dept", "")),
                  Field("target_user_id", "Or ask one person", "select", coerce=int, blank_label="—",
                        options=[(u.id, f"{u.name} ({u.department.name if u.department else 'No department'})") for u in people],
                        help="Choose either a department or a person. A question to one person is private to them."),
                  Field("subject", "Question", required=True, half=False), priority,
                  Field("description", "Details", "textarea", half=False, rows=5)]
    form = Form(fields)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        if stored == "query" and not (d["target_department_id"] or d["target_user_id"]):
            form.fields[0].error = "Choose a department or a person to ask."
        elif stored == "query" and d["target_department_id"] and d["target_user_id"]:
            form.fields[1].error = "Choose a department or a person, not both."
        else:
            t = Ticket(kind=stored, requester_id=current_user.id, status="open", **d)
            if stored == "query":
                t.category = "General"
            db.session.add(t)
            db.session.flush()
            add_comment("ticket", t.id, f"{TICKET_KINDS[stored]} raised by {current_user.name}.", kind="status")
            log_activity("created", "ticket", t.id, f"Raised {t.number}: {t.subject}")
            notify(_staff_of_kind(stored, t.target_department_id, t.target_user_id), f"New {TICKET_KINDS[stored].lower()}: {t.number}",
                   t.subject, url_for("helpdesk.detail", tid=t.id), kind="ticket")
            db.session.commit()
            flash(f"{t.number} was created.", "success")
            return redirect(url_for("helpdesk.detail", tid=t.id))
    subtitles = {"csr": "Log a customer complaint or request.", "it": "Tell IT what's wrong.", "query": "Send a question to a department or a person."}
    return render_template("form_page.html", form=form, title={"csr": "New CSR ticket", "it": "New IT ticket", "query": "New query"}[stored],
                           subtitle=subtitles[stored], submit="Submit", cancel=url_for("helpdesk.index", kind=kind))


@bp.route("/ticket/<int:tid>")
@access("tickets.create")
def detail(tid):
    t = visible_tickets(current_user).filter(Ticket.id == tid).first_or_404()
    g.helpdesk_kind = KIND_TO_URL[t.kind]
    staff = is_ticket_staff(current_user, t)
    return render_template("helpdesk/detail.html", t=t, staff=staff, comments=comments_for("ticket", t.id, include_internal=staff),
                           pool=_staff_pool(t) if staff else [], statuses=TICKET_STATUSES, priorities=PRIORITIES,
                           is_requester=t.requester_id == current_user.id, url_kind=KIND_TO_URL[t.kind])


@bp.route("/ticket/<int:tid>/reply", methods=["POST"])
@access("tickets.create")
def reply(tid):
    t = visible_tickets(current_user).filter(Ticket.id == tid).first_or_404()
    staff = is_ticket_staff(current_user, t)
    body = request.form.get("body", "").strip()
    if not body:
        return redirect(url_for("helpdesk.detail", tid=t.id))
    internal = staff and request.form.get("internal") == "1"
    add_comment("ticket", t.id, body, internal=internal)
    link = url_for("helpdesk.detail", tid=t.id)
    if not internal:
        if current_user.id == t.requester_id:
            notify([x for x in (t.assignee_id, t.target_user_id) if x], f"Reply on {t.number}", body[:150], link, kind="ticket")
            if t.status in ("waiting", "resolved"):
                t.status = "in_progress"
        else:
            notify(t.requester_id, f"Reply on {t.number}", body[:150], link, kind="ticket")
            if t.status == "open":
                t.status = "in_progress"
            if t.assignee_id is None and t.kind != "query":
                t.assignee_id = current_user.id
    t.updated_at = now()
    db.session.commit()
    return redirect(link + "#comments")


@bp.route("/ticket/<int:tid>/update", methods=["POST"])
@access("tickets.create")
def update(tid):
    t = visible_tickets(current_user).filter(Ticket.id == tid).first_or_404()
    staff = is_ticket_staff(current_user, t)
    is_requester = t.requester_id == current_user.id
    link = url_for("helpdesk.detail", tid=t.id)
    new_status = request.form.get("status", t.status)
    valid_status = {k for k, _ in TICKET_STATUSES}
    if new_status not in valid_status:
        abort(400)
    if not staff:
        # requesters may only close their own ticket or reopen a finished one
        if not (is_requester and ((new_status == "closed") or (new_status == "open" and t.status in ("resolved", "closed")))):
            abort(403)
    if new_status != t.status:
        add_comment("ticket", t.id, f"Status changed from {label(t.status)} to {label(new_status)}.", kind="status")
        t.resolved_at = now() if new_status in ("resolved", "closed") else None
        if t.requester_id != current_user.id:
            notify(t.requester_id, f"{t.number} is {label(new_status).lower()}", t.subject, link, kind="ticket")
        t.status = new_status
    if staff:
        prio = request.form.get("priority", t.priority)
        if prio in {k for k, _ in PRIORITIES}:
            t.priority = prio
        assignee_id = request.form.get("assignee_id", type=int)
        if assignee_id and assignee_id != t.assignee_id and assignee_id in {u.id for u in _staff_pool(t)}:
            t.assignee_id = assignee_id
            person = db.session.get(User, assignee_id)
            add_comment("ticket", t.id, f"Assigned to {person.name}.", kind="status")
            notify(person, f"{t.number} assigned to you", t.subject, link, kind="ticket")
    t.updated_at = now()
    log_activity("updated", "ticket", t.id, f"Updated {t.number} ({label(t.status)})")
    db.session.commit()
    flash("Ticket updated.", "success")
    return redirect(link)
