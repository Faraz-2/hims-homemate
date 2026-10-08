"""The Files area: upload anything (see config.py for the few blocked program types), keep it in tidy folders.

* "My files"      everything you uploaded here
* "Department"    files shared with your department(s)
* "Company"       files shared with everyone
* "People"        for team leaders and admins: browse by department, then person

On disk every file sits in ``instance/uploads/departments/<department>/<person>/<year>/<month>/files/`` and every
organised backup contains the same files sorted the same way.
"""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..constants import FILE_FOLDERS, FILE_VISIBILITY
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Department, StoredFile, User
from ..scopes import user_department_ids, visible_files
from ..security import access, dept_ok, has_perm
from ..services import delete_upload, log_activity, paginate, save_upload, upload_limit_text, upload_path
from ..storage import blocked_list_text

bp = Blueprint("files", __name__, url_prefix="/files")

VIEWS = (("mine", "My files"), ("department", "My department"), ("company", "Company"), ("people", "People"))


def _clean_folder(raw):
    raw = " ".join((raw or "").replace("/", " ").replace("\\", " ").split())[:80]
    return raw or "General"


def _can_share_company(user):
    return user.is_manager_level or has_perm(user, "files.manage")


def _visibility_options(user):
    opts = [("private", FILE_VISIBILITY[0][1])]
    if user_department_ids(user):
        opts.append(("department", FILE_VISIBILITY[1][1]))
    if _can_share_company(user):
        opts.append(("company", FILE_VISIBILITY[2][1]))
    return opts


def _can_open(sf):
    if sf.entity_type == "vendor":
        if has_perm(current_user, "vendors.view") and dept_ok(current_user, "vendors"):
            return True
    return visible_files(current_user).filter(StoredFile.id == sf.id).first() is not None


def _can_change(sf):
    if sf.owner_id == current_user.id or current_user.is_admin_level:
        return True
    if sf.entity_type == "vendor":
        return has_perm(current_user, "vendors.manage") and dept_ok(current_user, "vendors")
    return has_perm(current_user, "files.manage") and visible_files(current_user).filter(StoredFile.id == sf.id).first() is not None


def _people_scope():
    """Departments whose people this user may browse in the People view."""
    if current_user.is_admin_level:
        return [d.id for d in Department.query.filter_by(active=True).all()]
    return sorted(current_user.led_department_ids)


@bp.route("/")
@access("files.view")
def index():
    view = request.args.get("view", "mine")
    text = request.args.get("q", "").strip()
    folder = request.args.get("folder", "")
    person = request.args.get("person", type=int)
    start, end = request.args.get("from", ""), request.args.get("to", "")
    people_scope = _people_scope()
    if view == "people" and not people_scope:
        view = "mine"

    q = visible_files(current_user).filter(StoredFile.entity_type.is_(None))
    if view == "mine":
        q = q.filter(StoredFile.owner_id == current_user.id)
    elif view == "department":
        mine = user_department_ids(current_user)
        q = q.filter(StoredFile.visibility == "department")
        if not current_user.is_admin_level:
            q = q.filter(StoredFile.department_id.in_(mine or [0]))
    elif view == "company":
        q = q.filter(StoredFile.visibility == "company")
    elif view == "people":
        q = q.filter(StoredFile.department_id.in_(people_scope or [0]))
        if person:
            q = q.filter(StoredFile.owner_id == person)
    if text:
        like = f"%{text}%"
        q = q.filter(or_(StoredFile.title.ilike(like), StoredFile.original_name.ilike(like), StoredFile.description.ilike(like)))
    if folder:
        q = q.filter(StoredFile.folder == folder)
    for raw, op in ((start, "ge"), (end, "le")):
        try:
            d = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            continue
        if op == "ge":
            q = q.filter(StoredFile.created_at >= d)
        else:
            q = q.filter(StoredFile.created_at < d.replace(hour=23, minute=59, second=59))

    folders = [f for (f,) in visible_files(current_user).filter(StoredFile.entity_type.is_(None))
               .with_entities(StoredFile.folder).distinct().order_by(StoredFile.folder).all()]

    directory = None
    if view == "people" and not person:
        totals = {}
        for f in q.all():
            row = totals.setdefault((f.department_id, f.owner_id), {"user": f.owner, "files": 0, "bytes": 0})
            row["files"] += 1
            row["bytes"] += f.size or 0
        depts = {d.id: d for d in Department.query.all()}
        grouped = {}
        for (dept_id, _owner), row in totals.items():
            grouped.setdefault(depts.get(dept_id), []).append(row)
        directory = sorted(((d, sorted(rows, key=lambda r: r["user"].name)) for d, rows in grouped.items()),
                           key=lambda pair: pair[0].name if pair[0] else "")

    pager = None
    if directory is None:
        pager = paginate(q.order_by(StoredFile.created_at.desc()), 25)
    chosen = db.session.get(User, person) if person else None
    return render_template("files/index.html", pager=pager, view=view, views=[v for v in VIEWS if v[0] != "people" or people_scope],
                           q=text, folder=folder, folders=folders, start=start, end=end, directory=directory, chosen=chosen,
                           can_upload=has_perm(current_user, "files.upload"), can_change=_can_change)


@bp.route("/upload", methods=["GET", "POST"])
@access("files.upload")
def upload():
    options = _visibility_options(current_user)
    known = [f for (f,) in db.session.query(StoredFile.folder).filter(StoredFile.owner_id == current_user.id).distinct().all()]
    folders = list(dict.fromkeys(FILE_FOLDERS + sorted(known)))
    values = {"title": "", "folder": "General", "visibility": "private", "description": ""}
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k in values}
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if values["visibility"] not in {k for k, _ in options}:
            values["visibility"] = "private"
        if not files:
            flash("Choose at least one file to upload.", "error")
        else:
            folder = _clean_folder(values["folder"])
            saved, problems = 0, []
            for f in files:
                try:
                    stored, original, size = save_upload(f, "files")
                except ValueError as exc:
                    problems.append(f"{f.filename}: {exc}")
                    continue
                sf = StoredFile(title=(values["title"] if len(files) == 1 and values["title"] else original)[:200],
                                description=values["description"][:400] or None, folder=folder, original_name=original,
                                file_name=stored, size=size, visibility=values["visibility"], owner_id=current_user.id,
                                department_id=current_user.department_id)
                db.session.add(sf)
                db.session.flush()
                log_activity("uploaded", "file", sf.id, f'Uploaded "{sf.title}" to {folder}')
                saved += 1
            db.session.commit()
            if saved:
                flash(f"{saved} file(s) uploaded to the “{folder}” folder.", "success")
            for p in problems:
                flash(p, "error")
            if saved:
                return redirect(url_for("files.index"))
    return render_template("files/upload.html", options=options, folders=folders, v=values, limit=upload_limit_text(),
                           blocked=blocked_list_text())


@bp.route("/<int:fid>/download")
@access("files.view")
def download(fid):
    sf = db.session.get(StoredFile, fid) or abort(404)
    if not _can_open(sf):
        abort(404)
    try:
        path = upload_path(sf.file_name)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=sf.original_name)


@bp.route("/<int:fid>/edit", methods=["GET", "POST"])
@access("files.view")
def edit(fid):
    sf = db.session.get(StoredFile, fid) or abort(404)
    if not (_can_open(sf) and _can_change(sf)):
        abort(403)
    options = _visibility_options(current_user)
    if sf.visibility not in {k for k, _ in options}:
        options.append((sf.visibility, dict(FILE_VISIBILITY).get(sf.visibility, sf.visibility)))
    form = Form([
        Field("title", "Title", required=True),
        Field("folder", "Folder", required=True, help="Type a new name to start a new folder."),
        Field("visibility", "Who can see it?", "select", required=True, options=options),
        Field("description", "Description", half=False),
    ]).fill(sf)
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        sf.title, sf.folder, sf.visibility, sf.description = d["title"], _clean_folder(d["folder"]), d["visibility"], d["description"]
        log_activity("updated", "file", sf.id, f'Edited file "{sf.title}"')
        db.session.commit()
        flash("File details saved.", "success")
        return redirect(request.args.get("next") or url_for("files.index"))
    return render_template("form_page.html", form=form, title=f"Edit: {sf.title}", subtitle=sf.original_name,
                           submit="Save changes", cancel=url_for("files.index"))


@bp.route("/<int:fid>/delete", methods=["POST"])
@access("files.view")
def delete(fid):
    sf = db.session.get(StoredFile, fid) or abort(404)
    if not (_can_open(sf) and _can_change(sf)):
        abort(403)
    delete_upload(sf.file_name)
    log_activity("deleted", "file", sf.id, f'Deleted file "{sf.title}"')
    back = request.form.get("next") or url_for("files.index")
    db.session.delete(sf)
    db.session.commit()
    flash("File deleted.", "success")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("files.index"))


# ---------------------------------------------------------------- files attached to a record (vendors)
@bp.route("/attach/vendor/<int:vid>", methods=["POST"])
@access("vendors.manage", "vendors")
def attach_vendor(vid):
    from ..models import Vendor
    vendor = Vendor.query.get_or_404(vid)
    back = url_for("vendors.detail", vid=vendor.id) + "#files"
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose at least one file to attach.", "error")
        return redirect(back)
    folder = _clean_folder(request.form.get("folder") or "Vendor documents")
    saved = 0
    for f in files:
        try:
            stored, original, size = save_upload(f, "vendors")
        except ValueError as exc:
            flash(f"{f.filename}: {exc}", "error")
            continue
        db.session.add(StoredFile(title=original[:200], folder=folder, original_name=original, file_name=stored, size=size,
                                  visibility="department", owner_id=current_user.id, department_id=current_user.department_id,
                                  entity_type="vendor", entity_id=vendor.id))
        saved += 1
    if saved:
        log_activity("uploaded", "vendor", vendor.id, f"Attached {saved} file(s) to vendor {vendor.name}")
        db.session.commit()
        flash(f"{saved} file(s) attached to {vendor.name}.", "success")
    return redirect(back)
