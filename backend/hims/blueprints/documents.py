from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..constants import DOC_CATEGORIES
from ..extensions import db
from ..forms import Field, Form, strings
from ..models import Department, Document
from ..scopes import visible_documents
from ..security import access, has_perm
from ..services import delete_upload, log_activity, paginate, save_upload, upload_limit_text, upload_path

bp = Blueprint("documents", __name__, url_prefix="/documents")


@bp.route("/")
@access("documents.view")
def index():
    text = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    q = visible_documents(current_user)
    if text:
        like = f"%{text}%"
        q = q.filter(or_(Document.title.ilike(like), Document.description.ilike(like), Document.original_name.ilike(like)))
    if category:
        q = q.filter(Document.category == category)
    pager = paginate(q.order_by(Document.created_at.desc()), 20)
    return render_template("documents/index.html", pager=pager, q=text, category=category, categories=DOC_CATEGORIES,
                           can_upload=has_perm(current_user, "documents.upload"),
                           can_delete=lambda d: d.uploaded_by_id == current_user.id or has_perm(current_user, "documents.manage"))


@bp.route("/upload", methods=["GET", "POST"])
@access("documents.upload")
def upload():
    audience = []
    if current_user.department_id:
        audience.append(("dept", f"{current_user.department.name} only"))
    if current_user.is_manager_level or not audience:
        audience.insert(0, ("company", "Whole company"))
    form = Form([
        Field("title", "Title", required=True),
        Field("category", "Category", "select", options=strings(DOC_CATEGORIES)),
        Field("audience", "Who can see it?", "select", required=True, options=audience, value=audience[0][0]),
        Field("description", "Description", half=False),
        Field("file", "File", "file", half=False, help=f"PDF, images, videos, Office files, CSV or zip, up to {upload_limit_text()}."),
    ])
    if request.method == "POST" and form.validate(request.form):
        d = form.data
        try:
            upload_file = request.files.get("file")
            if not upload_file or not upload_file.filename:
                raise ValueError("Choose a file to upload.")
            stored, original, size = save_upload(upload_file, "documents")
        except ValueError as exc:
            form.fields[-1].error = str(exc)
        else:
            doc = Document(title=d["title"], category=d["category"], description=d["description"], file_name=stored,
                           original_name=original, size=size, uploaded_by_id=current_user.id,
                           department_id=current_user.department_id if d["audience"] == "dept" else None)
            db.session.add(doc)
            db.session.flush()
            log_activity("uploaded", "document", doc.id, f'Uploaded "{doc.title}"')
            db.session.commit()
            flash("Document uploaded.", "success")
            return redirect(url_for("documents.index"))
    return render_template("form_page.html", form=form, title="Upload a document",
                           subtitle="Shared files stay on the HIMS server and are included in backups.",
                           submit="Upload", cancel=url_for("documents.index"))


@bp.route("/<int:did>/download")
@access("documents.view")
def download(did):
    doc = visible_documents(current_user).filter(Document.id == did).first_or_404()
    try:
        path = upload_path(doc.file_name)
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=doc.original_name)


@bp.route("/<int:did>/delete", methods=["POST"])
@access("documents.view")
def delete(did):
    doc = visible_documents(current_user).filter(Document.id == did).first_or_404()
    if not (doc.uploaded_by_id == current_user.id or has_perm(current_user, "documents.manage")):
        abort(403)
    delete_upload(doc.file_name)
    log_activity("deleted", "document", doc.id, f'Deleted "{doc.title}"')
    db.session.delete(doc)
    db.session.commit()
    flash("Document deleted.", "success")
    return redirect(url_for("documents.index"))
