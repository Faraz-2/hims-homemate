"""Kanban-style pipelines. One factory builds both the sales lead pipeline and the recruitment pipeline."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from ..constants import CANDIDATE_STAGES, COHORTS, GATES, LEAD_SOURCES, LEAD_STAGES, STARTUP_SECTORS, STARTUP_STAGES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Campaign, Candidate, Comment, Department, Lead, Startup, User
from ..security import access
from ..services import add_comment, comments_for, get_setting, log_activity, notify
from ..timeutil import today
from ..ui import datefmt, label, number

RECRUIT_SOURCES = ["LinkedIn", "Rozee.pk", "Referral", "Walk-in", "Website", "Other"]


class Pipeline:
    def __init__(self, name, prefix, title, singular, model, stages, gate, view_perm, manage_perm, delete_perm, icon):
        self.name, self.prefix, self.title, self.singular = name, prefix, title, singular
        self.model, self.stages, self.gate = model, stages, gate
        self.view_perm, self.manage_perm, self.delete_perm, self.icon = view_perm, manage_perm, delete_perm, icon
        self.stage_keys = [k for k, _ in stages]

    def ep(self, name):
        return f"{self.name}.{name}"

    # -- who can work on the pipeline
    def owners(self):
        slugs = GATES[self.gate]
        return (User.query.join(Department, User.department_id == Department.id)
                .filter(User.active.is_(True), Department.slug.in_(slugs)).order_by(User.name).all())

    def can_edit(self, obj):
        return current_user.is_manager_level or obj.owner_id in (None, current_user.id)


# ------------------------------------------------------------------ pipeline-specific pieces
def lead_fields(cfg):
    depts = Department.query.filter(Department.slug.in_(GATES["leads"])).order_by(Department.name).all()
    owners = cfg.owners()
    return [
        Field("name", "Contact name", required=True),
        Field("company", "Company"),
        Field("phone", "Phone", "tel"),
        Field("email", "Email", "email"),
        Field("source", "Source", "select", options=strings(LEAD_SOURCES)),
        Field("stage", "Stage", "select", required=True, options=LEAD_STAGES, value="new"),
        Field("value", "Expected value", "number", min=0, placeholder="0"),
        Field("next_follow_up", "Next follow-up", "date"),
        Field("department_id", "Owning department", "select", required=True, coerce=int, options=[(d.id, d.name) for d in depts]),
        Field("owner_id", "Lead owner", "select", coerce=int, options=[(u.id, u.name) for u in owners], blank_label="Unassigned"),
        Field("campaign_id", "Came from campaign", "select", coerce=int, blank_label="Not from a campaign",
              options=[(c.id, c.name) for c in Campaign.query.order_by(Campaign.name).all()],
              help="Links this lead to the campaign that generated it, so campaign results are measured from real leads."),
        Field("notes", "Notes", "textarea", half=False, rows=3),
    ]


def candidate_fields(cfg):
    depts = Department.query.filter_by(active=True).order_by(Department.name).all()
    owners = cfg.owners()
    return [
        Field("name", "Candidate name", required=True),
        Field("position", "Position applied for", required=True),
        Field("department_id", "Hiring department", "select", coerce=int, options=[(d.id, d.name) for d in depts]),
        Field("stage", "Stage", "select", required=True, options=CANDIDATE_STAGES, value="applied"),
        Field("email", "Email", "email"),
        Field("phone", "Phone", "tel"),
        Field("source", "Source", "select", options=strings(RECRUIT_SOURCES)),
        Field("expected_salary", "Expected salary", "number", min=0),
        Field("owner_id", "Recruiter", "select", coerce=int, options=[(u.id, u.name) for u in owners], blank_label="Unassigned"),
        Field("notes", "Notes", "textarea", half=False, rows=3),
    ]


def lead_card(l):
    return {"id": l.id, "title": l.name, "subtitle": l.company or "No company",
            "lines": [x for x in (l.phone, l.owner.name if l.owner else "Unassigned") if x],
            "amount": l.value or 0, "amount_kind": "money",
            "due": l.next_follow_up, "due_label": "Follow-up",
            "overdue": bool(l.next_follow_up and l.next_follow_up < today() and l.stage not in ("won", "lost")),
            "tag": l.department.name if l.department else None}


def candidate_card(c):
    return {"id": c.id, "title": c.name, "subtitle": c.position,
            "lines": [x for x in (c.department.name if c.department else None, c.source) if x],
            "amount": c.expected_salary or 0, "amount_kind": "money", "due": None, "due_label": "", "overdue": False,
            "tag": c.owner.name if c.owner else "Unassigned"}


def lead_info(l):
    return [("Company", l.company), ("Phone", l.phone), ("Email", l.email), ("Source", l.source),
            ("Expected value", f"{get_setting('currency', 'PKR')} {number(l.value)}"),
            ("Next follow-up", datefmt(l.next_follow_up)), ("Owning department", l.department.name if l.department else None),
            ("Lead owner", l.owner.name if l.owner else "Unassigned"),
            ("Campaign", l.campaign.name if l.campaign else None), ("Notes", l.notes)]


def candidate_info(c):
    return [("Position", c.position), ("Hiring department", c.department.name if c.department else None), ("Email", c.email),
            ("Phone", c.phone), ("Source", c.source),
            ("Expected salary", f"{get_setting('currency', 'PKR')} {number(c.expected_salary)}" if c.expected_salary else None),
            ("Recruiter", c.owner.name if c.owner else "Unassigned"), ("Notes", c.notes)]


def build(cfg, fields_fn, card_fn, info_fn, filter_dept=False):
    bp = Blueprint(cfg.name, __name__, url_prefix=cfg.prefix)
    Model = cfg.model

    def _get(iid):
        return Model.query.get_or_404(iid)

    def board():
        text = request.args.get("q", "").strip()
        owner = request.args.get("owner", "")
        dept = request.args.get("dept", type=int)
        q = Model.query
        if text:
            like = f"%{text}%"
            q = q.filter(or_(Model.name.ilike(like), (Model.company if hasattr(Model, "company") else Model.position).ilike(like)))
        if owner == "mine":
            q = q.filter(Model.owner_id == current_user.id)
        if dept and filter_dept:
            q = q.filter(Model.department_id == dept)
        items = q.order_by(Model.updated_at.desc()).all()
        columns = []
        for key, name in cfg.stages:
            col = [i for i in items if i.stage == key]
            columns.append({"key": key, "label": name, "count": len(col), "cards": [card_fn(i) for i in col],
                            "total": sum((getattr(i, "value", None) or getattr(i, "expected_salary", None) or 0) for i in col)})
        depts = Department.query.filter(Department.slug.in_(GATES["leads"])).order_by(Department.name).all() if filter_dept else []
        return render_template("pipeline/board.html", cfg=cfg, columns=columns, q=text, owner=owner, dept=dept,
                               departments=depts, total=len(items))

    def _form(obj=None):
        form = Form(fields_fn(cfg))
        if obj is not None:
            form.fill(obj)
        else:
            for f in form.fields:
                if f.name == "owner_id":
                    f.value = current_user.id
                if f.name == "department_id" and cfg.name == "leads":
                    f.value = current_user.department_id or ""
        return form

    def _apply(obj, data):
        for key, value in data.items():
            setattr(obj, key, value)
        if hasattr(obj, "value") and obj.value is None:
            obj.value = 0

    def new():
        form = _form()
        if request.method == "POST" and form.validate(request.form):
            obj = Model()
            _apply(obj, form.data)
            db.session.add(obj)
            db.session.flush()
            log_activity("created", cfg.name, obj.id, f"Added {cfg.singular} {obj.name}")
            if obj.owner_id:
                notify(obj.owner_id, f"New {cfg.singular} assigned", f"{obj.name} was added to your pipeline.",
                       url_for(cfg.ep("detail"), iid=obj.id), kind="pipeline")
            db.session.commit()
            flash(f"{cfg.singular.capitalize()} added.", "success")
            return redirect(url_for(cfg.ep("detail"), iid=obj.id))
        return render_template("form_page.html", form=form, title=f"Add {cfg.singular}", subtitle=cfg.title,
                               submit=f"Add {cfg.singular}", cancel=url_for(cfg.ep("board")))

    def detail(iid):
        obj = _get(iid)
        return render_template("pipeline/detail.html", cfg=cfg, obj=obj, info=info_fn(obj),
                               comments=comments_for(cfg.name, obj.id), can_edit=cfg.can_edit(obj),
                               can_delete=current_user.is_manager_level)

    def edit(iid):
        obj = _get(iid)
        if not cfg.can_edit(obj):
            abort(403)
        form = _form(obj)
        if request.method == "POST" and form.validate(request.form):
            _apply(obj, form.data)
            log_activity("updated", cfg.name, obj.id, f"Updated {cfg.singular} {obj.name}")
            db.session.commit()
            flash("Saved.", "success")
            return redirect(url_for(cfg.ep("detail"), iid=obj.id))
        return render_template("form_page.html", form=form, title=f"Edit {cfg.singular}", subtitle=obj.name,
                               submit="Save changes", cancel=url_for(cfg.ep("detail"), iid=obj.id))

    def move(iid):
        obj = _get(iid)
        if not cfg.can_edit(obj):
            abort(403)
        stage = request.form.get("stage", "")
        if stage not in cfg.stage_keys:
            abort(400)
        if stage != obj.stage:
            old = obj.stage
            obj.stage = stage
            add_comment(cfg.name, obj.id, f"Stage changed from {label(old)} to {label(stage)}.", kind="status")
            log_activity("stage", cfg.name, obj.id, f"{obj.name}: {label(old)} → {label(stage)}")
            if obj.owner_id:
                notify(obj.owner_id, f"{cfg.singular.capitalize()} moved to {label(stage)}", obj.name,
                       url_for(cfg.ep("detail"), iid=obj.id), kind="pipeline")
            db.session.commit()
        return redirect(request.referrer or url_for(cfg.ep("board")))

    def comment(iid):
        obj = _get(iid)
        body = request.form.get("body", "").strip()
        if body:
            add_comment(cfg.name, obj.id, body)
            db.session.commit()
        return redirect(url_for(cfg.ep("detail"), iid=obj.id) + "#comments")

    def delete(iid):
        obj = _get(iid)
        if not current_user.is_manager_level:
            abort(403)
        Comment.query.filter_by(entity_type=cfg.name, entity_id=obj.id).delete()
        log_activity("deleted", cfg.name, obj.id, f"Deleted {cfg.singular} {obj.name}")
        db.session.delete(obj)
        db.session.commit()
        flash(f"{cfg.singular.capitalize()} deleted.", "success")
        return redirect(url_for(cfg.ep("board")))

    view = access(cfg.view_perm, cfg.gate)
    manage = access(cfg.manage_perm, cfg.gate)
    remove = access(cfg.delete_perm, cfg.gate)
    bp.add_url_rule("/", "board", view(board))
    bp.add_url_rule("/new", "new", manage(new), methods=["GET", "POST"])
    bp.add_url_rule("/<int:iid>", "detail", view(detail))
    bp.add_url_rule("/<int:iid>/edit", "edit", manage(edit), methods=["GET", "POST"])
    bp.add_url_rule("/<int:iid>/move", "move", manage(move), methods=["POST"])
    bp.add_url_rule("/<int:iid>/comment", "comment", manage(comment), methods=["POST"])
    bp.add_url_rule("/<int:iid>/delete", "delete", remove(delete), methods=["POST"])
    return bp


_leads_cfg = Pipeline("leads", "/leads", "Sales and marketing lead pipeline", "lead", Lead, LEAD_STAGES,
                      "leads", "leads.view", "leads.manage", "leads.delete", "trending-up")
_recruit_cfg = Pipeline("recruitment", "/recruitment", "Recruitment pipeline", "candidate", Candidate, CANDIDATE_STAGES,
                        "recruitment", "recruitment.view", "recruitment.manage", "recruitment.manage", "user-check")

leads_bp = build(_leads_cfg, lead_fields, lead_card, lead_info, filter_dept=True)
recruitment_bp = build(_recruit_cfg, candidate_fields, candidate_card, candidate_info)


# ------------------------------------------------------------------ NIC Hyderabad: startup incubation
def startup_fields(cfg):
    owners = cfg.owners()
    return [
        Field("name", "Startup name", required=True),
        Field("founder_name", "Founder name"),
        Field("company", "Legal company name (if different)"),
        Field("phone", "Phone", "tel"),
        Field("email", "Email", "email"),
        Field("sector", "Sector", "select", options=strings(STARTUP_SECTORS)),
        Field("cohort", "Cohort", "select", options=strings(COHORTS)),
        Field("stage", "Stage", "select", required=True, options=STARTUP_STAGES, value="applied"),
        Field("value", "Investment / grant under discussion", "number", min=0, placeholder="0"),
        Field("next_follow_up", "Next follow-up", "date"),
        Field("owner_id", "Assigned mentor", "select", coerce=int, options=[(u.id, u.name) for u in owners], blank_label="Unassigned"),
        Field("notes", "Notes", "textarea", half=False, rows=3),
    ]


def startup_card(s):
    return {"id": s.id, "title": s.name, "subtitle": s.founder_name or "No founder on file",
            "lines": [x for x in (s.sector, s.owner.name if s.owner else "No mentor assigned") if x],
            "amount": s.value or 0, "amount_kind": "money",
            "due": s.next_follow_up, "due_label": "Follow-up",
            "overdue": bool(s.next_follow_up and s.next_follow_up < today() and s.stage not in ("graduated", "rejected")),
            "tag": s.cohort}


def startup_info(s):
    return [("Founder", s.founder_name), ("Legal company name", s.company), ("Phone", s.phone), ("Email", s.email),
            ("Sector", s.sector), ("Cohort", s.cohort),
            ("Investment / grant under discussion", f"{get_setting('currency', 'PKR')} {number(s.value)}" if s.value else None),
            ("Next follow-up", datefmt(s.next_follow_up)), ("Assigned mentor", s.owner.name if s.owner else "Unassigned"),
            ("Notes", s.notes)]


_incubation_cfg = Pipeline("incubation", "/incubation", "NIC Hyderabad startup incubation pipeline", "startup", Startup,
                           STARTUP_STAGES, "incubation", "incubation.view", "incubation.manage", "incubation.manage", "rocket")
incubation_bp = build(_incubation_cfg, startup_fields, startup_card, startup_info)
