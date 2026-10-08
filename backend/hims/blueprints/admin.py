import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func

from ..constants import DEFAULT_SETTINGS, ROLES, TIERS
from ..extensions import db
from ..forms import Field, Form
from ..models import ActivityLog, Department, DocumentType, Permission, Role, User
from datetime import datetime
from ..security import access, has_perm
from ..seed import apply_default_permissions
from .. import backup_vault
from ..services import (BackupError, backup_dir, create_backup, database_size, get_setting, lan_ip, list_backups,
                        log_activity, paginate, secondary_backup_status, set_setting)
from ..timeutil import TZ_NAME, now

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _any_admin_perm():
    return any(has_perm(current_user, c) for c in ("admin.settings", "admin.roles", "admin.backup", "activity.view"))


@bp.route("/")
@access()
def index():
    if not _any_admin_perm():
        abort(403)
    cfg = current_app.config
    backups = list_backups()
    upload_root = Path(cfg["UPLOAD_DIR"])
    files = [p for p in upload_root.rglob("*") if p.is_file()] if upload_root.exists() else []
    online_cut = now() - timedelta(minutes=5)
    health = {
        "database": db.engine.url.get_backend_name().replace("postgresql", "PostgreSQL").replace("sqlite", "SQLite"),
        "db_size": database_size(),
        "uploads": (len(files), sum(f.stat().st_size for f in files)),
        "last_backup": backups[0] if backups else None,
        "backup_count": len(backups),
        "secondary": secondary_backup_status(),
        "users_active": User.query.filter_by(active=True).count(),
        "users_online": User.query.filter(User.last_seen_at >= online_cut).count(),
        "started": cfg["STARTED_AT"],
        "address": f"http://{lan_ip()}:{os.environ.get('HIMS_PORT', '8000')}",
        "timezone": TZ_NAME,
        "python": sys.version.split()[0],
        "version": cfg["VERSION"],
    }
    locked = User.query.filter(User.locked_until > now()).all()
    pending_pw = User.query.filter_by(active=True, must_change_password=True).count()
    return render_template("admin/index.html", health=health, locked=locked, pending_pw=pending_pw,
                           can_settings=has_perm(current_user, "admin.settings"), can_roles=has_perm(current_user, "admin.roles"),
                           can_backup=has_perm(current_user, "admin.backup"), can_activity=has_perm(current_user, "activity.view"))


@bp.route("/unlock/<int:uid>", methods=["POST"])
@access("employees.manage")
def unlock(uid):
    user = User.query.get_or_404(uid)
    user.locked_until, user.failed_logins = None, 0
    log_activity("unlocked", "user", user.id, f"Unlocked account of {user.name}")
    db.session.commit()
    flash(f"{user.name}'s account is unlocked.", "success")
    return redirect(url_for("admin.index"))


# ---------------------------------------------------------------- roles & permission matrix
@bp.route("/roles", methods=["GET", "POST"])
@access("admin.roles")
def roles():
    role_list = Role.query.order_by(Role.level.desc()).all()
    perms = Permission.query.order_by(Permission.module, Permission.id).all()
    if request.method == "POST":
        submitted = set(request.form.getlist("grant"))
        for role in role_list:
            if role.key == "super_admin":
                continue                      # the Super Admin always keeps full access
            role.permissions = [p for p in perms if f"{role.id}:{p.id}" in submitted]
        log_activity("updated", "roles", None, "Updated the role permission matrix")
        db.session.commit()
        flash("Permissions saved. They apply the next time each person loads a page.", "success")
        return redirect(url_for("admin.roles"))
    granted = {(r.id, p.id) for r in role_list for p in r.permissions}
    modules = {}
    for p in perms:
        modules.setdefault(p.module, []).append(p)
    counts = dict(db.session.query(User.role_id, func.count(User.id)).group_by(User.role_id).all())
    return render_template("admin/roles.html", roles=role_list, modules=modules, granted=granted, counts=counts)


# ---------------------------------------------------------------- create and edit roles
BUILT_IN = {key for key, *_ in ROLES}


def _role_form(role=None):
    built_in = role is not None and role.key in BUILT_IN
    tiers = [(lvl, text) for lvl, text in TIERS if current_user.is_super or lvl < current_user.role.level]
    fields = [Field("name", "Role name", required=True, placeholder="e.g. Senior Designer, HR Executive, Trainee"),
              Field("description", "What this role is for", half=False, help="Shown on the Roles and rights page.")]
    if not built_in:
        fields.insert(1, Field("level", "Access tier", "select", required=True, coerce=int, options=tiers,
                               help="The tier sets the baseline: team leads and above can review work and approve leave, "
                                    "heads and above manage a department. Fine-tune each right in the permission matrix afterwards."))
    return Form(fields)


def _role_key(name):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "role"
    key, n = base, 2
    while Role.query.filter_by(key=key).first():
        key, n = f"{base}_{n}", n + 1
    return key


@bp.route("/roles/manage")
@access("admin.roles")
def roles_manage():
    rows = [{"role": r, "users": User.query.filter_by(role_id=r.id).count(), "perms": len(r.permissions), "built_in": r.key in BUILT_IN}
            for r in Role.query.order_by(Role.level.desc(), Role.name).all()]
    return render_template("admin/roles_manage.html", rows=rows, tiers=dict(TIERS))


@bp.route("/roles/new", methods=["GET", "POST"])
@access("admin.roles")
def role_new():
    form = _role_form()
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        if Role.query.filter(func.lower(Role.name) == d["name"].lower()).first():
            form.fields[0].error = "A role with this name already exists."
        elif not current_user.is_super and d["level"] >= current_user.role.level:
            abort(403)
        else:
            role = Role(key=_role_key(d["name"]), name=d["name"], level=d["level"], description=d["description"])
            db.session.add(role)
            db.session.flush()
            apply_default_permissions(role)
            log_activity("created", "role", role.id, f"Created role {role.name}")
            db.session.commit()
            flash(f"Role '{role.name}' created with the standard rights for its tier. Fine-tune them in the permission matrix.", "success")
            return redirect(url_for("admin.roles"))
    return render_template("form_page.html", form=form, title="New role", subtitle="Create a job category with its own rights.",
                           submit="Create role", cancel=url_for("admin.roles_manage"))


@bp.route("/roles/<int:rid>/edit", methods=["GET", "POST"])
@access("admin.roles")
def role_edit(rid):
    role = db.session.get(Role, rid) or abort(404)
    if role.key == "super_admin":
        abort(403)
    form = _role_form(role).fill(role)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        clash = Role.query.filter(func.lower(Role.name) == d["name"].lower(), Role.id != role.id).first()
        if clash:
            form.fields[0].error = "A role with this name already exists."
        elif "level" in d and not current_user.is_super and d["level"] >= current_user.role.level:
            abort(403)
        else:
            role.name, role.description = d["name"], d["description"]
            if "level" in d:
                role.level = d["level"]
            log_activity("updated", "role", role.id, f"Edited role {role.name}")
            db.session.commit()
            flash("Role saved.", "success")
            return redirect(url_for("admin.roles_manage"))
    return render_template("form_page.html", form=form, title=f"Edit role: {role.name}",
                           subtitle="Built-in roles keep their tier; you can rename them and change the description." if role.key in BUILT_IN else "",
                           submit="Save role", cancel=url_for("admin.roles_manage"))


@bp.route("/roles/<int:rid>/delete", methods=["POST"])
@access("admin.roles")
def role_delete(rid):
    role = db.session.get(Role, rid) or abort(404)
    if role.key in BUILT_IN:
        flash("Built-in roles cannot be deleted.", "error")
    elif User.query.filter_by(role_id=role.id).count():
        flash("Some people still have this role. Move them to another role first.", "error")
    else:
        log_activity("deleted", "role", role.id, f"Deleted role {role.name}")
        db.session.delete(role)
        db.session.commit()
        flash("Role deleted.", "success")
    return redirect(url_for("admin.roles_manage"))


# ---------------------------------------------------------------- settings
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


@bp.route("/settings", methods=["GET", "POST"])
@access("admin.settings")
def settings():
    errors = {}
    values = {k: get_setting(k, "") for k in DEFAULT_SETTINGS}
    if request.method == "POST":
        f = request.form
        values.update({k: f.get(k, "").strip() for k in DEFAULT_SETTINGS if k not in ("working_days", "auto_backup")})
        days = sorted(set(f.getlist("working_days")))
        values["working_days"] = ",".join(d for d in days if d.isdigit() and 0 <= int(d) <= 6)
        if not values["company_name"]:
            errors["company_name"] = "Enter the company name."
        for key in ("work_start", "work_end"):
            if not _TIME.match(values[key]):
                errors[key] = "Use 24-hour time like 09:00."
        values["auto_backup"] = "1" if f.get("auto_backup") else "0"
        if not _TIME.match(values["auto_backup_time"] or "22:00"):
            errors["auto_backup_time"] = "Use 24-hour time like 22:00."
        values["auto_backup_time"] = values["auto_backup_time"] or "22:00"
        for key in ("late_grace_minutes", "leave_annual", "leave_sick", "leave_casual", "backup_keep", "backup_keep_runs"):
            if not values[key].isdigit():
                errors[key] = "Enter a whole number."
        if not values["working_days"]:
            errors["working_days"] = "Choose at least one working day."
        if not errors:
            for key, value in values.items():
                set_setting(key, value)
            log_activity("updated", "settings", None, "Changed system settings")
            db.session.commit()
            flash("Settings saved.", "success")
            return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", v=values, errors=errors,
                           days=[(str(i), n) for i, n in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))],
                           active_days=set(str(values["working_days"]).split(",")), timezone=TZ_NAME)


# ---------------------------------------------------------------- backups
@bp.route("/backups")
@access("admin.backup")
def backups():
    return render_template("admin/backups.html", backups=list_backups(), secondary=secondary_backup_status(),
                           runs=backup_vault.list_runs()[:5], auto_on=str(get_setting("auto_backup", "1")) == "1",
                           auto_time=get_setting("auto_backup_time", "22:00"),
                           secondary_path=get_setting("backup_secondary_path", ""), keep=get_setting("backup_keep", "14"),
                           is_sqlite=db.engine.url.get_backend_name() == "sqlite")


@bp.route("/backups/create", methods=["POST"])
@access("admin.backup")
def backup_create():
    try:
        path = create_backup()
    except BackupError as exc:
        flash(str(exc), "error")
    else:
        log_activity("backup", "system", None, f"Created backup {path.name}")
        db.session.commit()
        flash(f"Backup created: {path.name}. Copy it to an external drive or cloud folder too.", "success")
    return redirect(url_for("admin.backups"))


@bp.route("/backups/<name>")
@access("admin.backup")
def backup_download(name):
    if name not in {b["name"] for b in list_backups()}:
        abort(404)
    log_activity("backup_download", "system", None, f"Downloaded {name}")
    db.session.commit()
    return send_file(Path(backup_dir()) / name, as_attachment=True, download_name=name)


# ---------------------------------------------------------------- complete (department / user / date) backups
@bp.route("/backups/complete", methods=["POST"])
@access("admin.backup")
def backup_complete():
    manifest = backup_vault.create_complete_backup(trigger=f"manual by {current_user.name}")
    if manifest["errors"]:
        flash(f"Backup finished with {len(manifest['errors'])} problem(s): " + "; ".join(manifest["errors"][:3]), "error")
    else:
        flash(f"Complete backup done: full backup, {len(manifest['departments'])} department zips and {len(manifest['users'])} person zips.", "success")
    return redirect(url_for("admin.backup_runs"))


@bp.route("/backups/runs")
@access("admin.backup")
def backup_runs():
    return render_template("admin/backup_runs.html", runs=backup_vault.list_runs())


@bp.route("/backups/runs/<day>/<run_time>")
@access("admin.backup")
def backup_run(day, run_time):
    run = next((r for r in backup_vault.list_runs() if r["date"] == day and r["time"] == run_time), None)
    if run is None:
        abort(404)
    text = request.args.get("q", "").strip().lower()
    users = [u for u in run["manifest"].get("users", []) if not text or text in (u["name"] + " " + (u.get("code") or "") + " " + u.get("department", "")).lower()]
    return render_template("admin/backup_run.html", run=run, m=run["manifest"], users=users, q=text)


@bp.route("/backups/runs/<day>/<run_time>/<kind>/<name>")
@access("admin.backup")
def backup_run_file(day, run_time, kind, name):
    try:
        path = backup_vault.run_file(day, run_time, kind, name)
    except ValueError:
        abort(404)
    log_activity("backup_download", "system", None, f"Downloaded {kind}/{name} from {day} {run_time}")
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=f"{day}_{run_time}_{name}")


@bp.route("/backups/export", methods=["GET", "POST"])
@access("admin.backup")
def backup_export():
    """Make one backup on demand: a department, a person, or everyone, optionally between two dates."""
    from ..models import User as U
    import tempfile
    from pathlib import Path as P
    depts = Department.query.order_by(Department.name).all()
    people = U.query.order_by(U.name).all()
    if request.method == "POST":
        kind = request.form.get("kind", "all")
        def parse(key):
            try:
                return datetime.strptime(request.form.get(key, ""), "%Y-%m-%d").date()
            except ValueError:
                return None
        start, end = parse("start"), parse("end")
        if start and end and start > end:
            flash("The start date must not be after the end date.", "error")
            return redirect(url_for("admin.backup_export"))
        dept = db.session.get(Department, request.form.get("department", type=int) or 0)
        person = db.session.get(U, request.form.get("user", type=int) or 0)
        if kind == "department" and dept is None or kind == "user" and person is None or kind not in ("all", "department", "user"):
            flash("Choose a department or a person for this kind of backup.", "error")
            return redirect(url_for("admin.backup_export"))
        scope = backup_vault.Scope(kind, department=dept, user=person, start=start, end=end)
        out_dir = backup_vault.organized_root() / "on-demand"
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in sorted(out_dir.glob("*.zip"))[:-20]:        # keep the 20 newest on-demand exports
            old.unlink(missing_ok=True)
        name = f"{scope.file_stem}_{(start or 'start')}_to_{(end or 'now')}_{datetime.now():%Y%m%d-%H%M%S}.zip"
        info = backup_vault.build_scope_zip(out_dir / name, scope)
        log_activity("backup", "system", None, f"On-demand backup: {scope.label}" + (f" from {start}" if start else "") + (f" to {end}" if end else ""))
        db.session.commit()
        flash(f"Backup ready: {info['files']} file(s), {sum(info['tables'].values())} record(s).", "success")
        return redirect(url_for("admin.backup_export", ready=name))
    ready = request.args.get("ready", "")
    on_demand = backup_vault.organized_root() / "on-demand"
    existing = sorted((f for f in on_demand.glob("*.zip")), reverse=True)[:20] if on_demand.is_dir() else []
    return render_template("admin/backup_export.html", depts=depts, people=people, ready=ready,
                           existing=[{"name": f.name, "size": f.stat().st_size} for f in existing])


@bp.route("/backups/export/<name>")
@access("admin.backup")
def backup_export_file(name):
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_.\-]+\.zip$", name):
        abort(404)
    path = backup_vault.organized_root() / "on-demand" / name
    if not path.is_file():
        abort(404)
    log_activity("backup_download", "system", None, f"Downloaded on-demand backup {name}")
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=name)


@bp.route("/storage")
@access("admin.backup")
def storage_map():
    return render_template("admin/storage.html", info=backup_vault.storage_overview())


@bp.route("/backups/user/<int:uid>")
@access("admin.backup")
def backup_user_history(uid):
    """All organised backups of one person across every date (also linked from the person's profile)."""
    person = User.query.get_or_404(uid)
    return render_template("admin/backup_user.html", person=person, rows=backup_vault.user_history(person))


# ---------------------------------------------------------------- activity log
@bp.route("/activity")
@access("activity.view")
def activity():
    user_id = request.args.get("user", type=int)
    action = request.args.get("action", "")
    entity = request.args.get("entity", "")
    text = request.args.get("q", "").strip()
    q = ActivityLog.query
    if user_id:
        q = q.filter(ActivityLog.user_id == user_id)
    if action:
        q = q.filter(ActivityLog.action == action)
    if entity:
        q = q.filter(ActivityLog.entity_type == entity)
    if text:
        q = q.filter(ActivityLog.description.ilike(f"%{text}%"))
    pager = paginate(q.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()), 30)
    actions = [a for (a,) in db.session.query(ActivityLog.action).distinct().order_by(ActivityLog.action).all()]
    entities = [e for (e,) in db.session.query(ActivityLog.entity_type).filter(ActivityLog.entity_type.isnot(None)).distinct().order_by(ActivityLog.entity_type).all()]
    return render_template("admin/activity.html", pager=pager, user_id=user_id, action=action, entity=entity, q=text,
                           actions=actions, entities=entities, users=User.query.order_by(User.name).all())


# ---------------------------------------------------------------- employee document types
@bp.route("/document-types")
@access("employees.documents")
def document_types():
    types = DocumentType.query.order_by(DocumentType.category, DocumentType.name).all()
    return render_template("admin/document_types.html", types=types)


@bp.route("/document-types/new", methods=["GET", "POST"])
@access("employees.documents")
def document_type_new():
    form = Form([
        Field("name", "Document name", required=True, half=False, placeholder="e.g. Driving licence"),
        Field("category", "Category", placeholder="e.g. Identity, Education, Contract"),
        Field("required", "Required for every employee", "checkbox"), Field("expiry_required", "Has an expiry date", "checkbox"),
        Field("verification_required", "Needs HR verification after upload", "checkbox"),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        if DocumentType.query.filter(func.lower(DocumentType.name) == d["name"].lower()).first():
            form.fields[0].error = "A document type with this name already exists."
        else:
            dt = DocumentType(name=d["name"], category=d["category"] or "Other", required=d["required"],
                              expiry_required=d["expiry_required"], verification_required=d["verification_required"])
            db.session.add(dt)
            log_activity("created", "document_type", None, f"Added document type {dt.name}")
            db.session.commit()
            flash("Document type added.", "success")
            return redirect(url_for("admin.document_types"))
    return render_template("form_page.html", form=form, title="New employee document type",
                           subtitle="Appears on every employee's Documents tab.", submit="Add", cancel=url_for("admin.document_types"))


@bp.route("/document-types/<int:tid>/edit", methods=["GET", "POST"])
@access("employees.documents")
def document_type_edit(tid):
    dt = db.session.get(DocumentType, tid) or abort(404)
    form = Form([
        Field("name", "Document name", required=True, half=False),
        Field("category", "Category"),
        Field("required", "Required for every employee", "checkbox"),
        Field("expiry_required", "Has an expiry date", "checkbox"),
        Field("verification_required", "Needs HR verification after upload", "checkbox"),
    ]).fill(dt)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        clash = DocumentType.query.filter(func.lower(DocumentType.name) == d["name"].lower(), DocumentType.id != dt.id).first()
        if clash:
            form.fields[0].error = "A document type with this name already exists."
        else:
            for key, value in d.items():
                setattr(dt, key, value)
            log_activity("updated", "document_type", None, f"Edited document type {dt.name}")
            db.session.commit()
            flash("Saved.", "success")
            return redirect(url_for("admin.document_types"))
    return render_template("form_page.html", form=form, title=f"Edit: {dt.name}", subtitle="", submit="Save changes",
                           cancel=url_for("admin.document_types"))


@bp.route("/document-types/<int:tid>/toggle", methods=["POST"])
@access("employees.documents")
def document_type_toggle(tid):
    dt = db.session.get(DocumentType, tid) or abort(404)
    dt.active = not dt.active
    db.session.commit()
    flash(f"{dt.name} is now {'active' if dt.active else 'hidden'}.", "success")
    return redirect(url_for("admin.document_types"))
