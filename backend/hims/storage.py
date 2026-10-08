"""Where uploaded files are kept on disk, and how the folders are named.

Every new upload goes into a tidy tree under ``instance/uploads``::

    departments/<department>/<person>/<year>/<month>/<kind>/<id>_<original name>

* ``<department>``  the uploader's department key (``vendor-management``), or ``_company`` for people without one
* ``<person>``      name plus employee code (``aisha-hm-hod-0001``), so two people with the same name never mix
* ``<kind>``        what the file is for: ``tasks``, ``receipts``, ``vendors``, ``files`` ...

Files uploaded before version 1.5 stay exactly where they are (``tasks/<id>.pdf`` and so on) and keep working: the
database stores each file's path, so nothing has to be moved. The backup engine reads the database, so older files
still appear in the right department, person and date in the organised backups.
"""
import re
import uuid
from pathlib import Path

from flask import current_app, has_request_context
from flask_login import current_user

from .timeutil import now

_SAFE = re.compile(r"[^a-z0-9]+")


def slug(text, fallback="item", limit=40):
    """'Maryum Batool' -> 'maryum-batool' (safe on every operating system)."""
    out = _SAFE.sub("-", (text or "").lower()).strip("-")[:limit].strip("-")
    return out or fallback


def person_folder(user):
    """Folder name for one person: stays the same for life because the employee code never changes."""
    if user is None:
        return "_system"
    code = slug(user.employee_code or f"id{user.id}", limit=20)
    return f"{slug(user.name, 'person', 28)}-{code}"


def department_folder(user=None, department=None):
    dept = department or (user.department if user is not None else None)
    return dept.slug if dept is not None else "_company"


def acting_user():
    if has_request_context() and current_user and current_user.is_authenticated:
        return current_user
    return None


def organized_dir(kind, user=None, when=None):
    """Relative folder (using '/') for a new upload of the given kind by the given (or current) user."""
    user = user if user is not None else acting_user()
    when = when or now()
    return "/".join(["departments", department_folder(user), person_folder(user),
                     f"{when:%Y}", f"{when:%m}", slug(kind, "files", 30)])


def stored_filename(original):
    """A unique, readable name that keeps the original extension: 'a1b2c3d4e5f6_price-list.xlsx'."""
    stem, dot, ext = original.rpartition(".")
    if not dot:                       # no extension at all
        stem, ext = original, ""
    stem = slug(stem, "file", 60)
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:10]
    return f"{uuid.uuid4().hex[:12]}_{stem}" + (f".{ext}" if ext else "")


def uploads_root() -> Path:
    return Path(current_app.config["UPLOAD_DIR"])


def extension_allowed(ext):
    """Any file type is accepted, apart from programs and scripts that run when opened (configurable)."""
    cfg = current_app.config
    if cfg.get("ALLOW_ANY_FILE_TYPE", True):
        return ext not in cfg.get("BLOCKED_EXTENSIONS", set())
    from .constants import ALLOWED_UPLOADS          # strict mode: the original short list
    return ext in ALLOWED_UPLOADS


def blocked_list_text():
    return ", ".join(sorted(current_app.config.get("BLOCKED_EXTENSIONS", set())))
