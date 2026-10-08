"""Shared services used by every blueprint."""
import csv
import io
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Response, current_app, g, has_request_context, request
from flask_login import current_user
from werkzeug.utils import secure_filename

from .constants import ALLOWED_UPLOADS, DEFAULT_SETTINGS
from .extensions import db
from .models import ActivityLog, Comment, Notification, Role, Setting, User
from .timeutil import TZ, now


# ------------------------------------------------------------------ settings
def all_settings() -> dict:
    if "_settings" not in g:
        g._settings = {s.key: s.value for s in Setting.query.all()}
    return g._settings


def get_setting(key, default=None):
    value = all_settings().get(key)
    if value is None or value == "":
        value = DEFAULT_SETTINGS.get(key, default) if default is None else default
    return value


def get_int(key, default=0) -> int:
    try:
        return int(float(get_setting(key, default)))
    except (TypeError, ValueError):
        return default


def set_setting(key, value) -> None:
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    g.pop("_settings", None)


def working_days() -> set:
    raw = get_setting("working_days", "0,1,2,3,4")
    return {int(x) for x in str(raw).split(",") if x.strip().isdigit()}


def is_working_day(d: date) -> bool:
    return d.weekday() in working_days()


def count_working_days(start: date, end: date) -> int:
    days, d = 0, start
    while d <= end:
        if is_working_day(d):
            days += 1
        d += timedelta(days=1)
    return days


# ------------------------------------------------------------------ activity + notifications
def log_activity(action, entity_type=None, entity_id=None, description="", user=None):
    actor = user
    if actor is None and has_request_context() and current_user and current_user.is_authenticated:
        actor = current_user
    ip = request.remote_addr if has_request_context() else None
    db.session.add(ActivityLog(
        user_id=getattr(actor, "id", None), action=action, entity_type=entity_type,
        entity_id=entity_id, description=(description or "")[:400], ip_address=ip))


def notify(users, title, body="", url=None, kind="info", include_self=False):
    """Create a notification for each user (objects or ids). The acting user is skipped by default."""
    me = current_user.id if has_request_context() and current_user and current_user.is_authenticated else None
    seen = set()
    for u in users if isinstance(users, (list, tuple, set)) else [users]:
        uid = getattr(u, "id", u)
        if not uid or uid in seen or (uid == me and not include_self):
            continue
        seen.add(uid)
        db.session.add(Notification(user_id=uid, title=title[:200], body=(body or "")[:400], url=url, kind=kind))


def add_comment(entity_type, entity_id, body, kind="comment", internal=False, user=None):
    db.session.add(Comment(entity_type=entity_type, entity_id=entity_id, body=body, kind=kind,
                           internal=internal, user_id=getattr(user or current_user, "id", None)))


def next_submission_version(entity_type, entity_id):
    """1, 2, 3, ... — used so a resubmitted file is kept as a new version, never overwriting the last one."""
    last = (Comment.query.filter_by(entity_type=entity_type, entity_id=entity_id, kind="submission")
            .order_by(Comment.version.desc()).first())
    return (last.version + 1) if last and last.version else 1


def comments_for(entity_type, entity_id, include_internal=True):
    q = Comment.query.filter_by(entity_type=entity_type, entity_id=entity_id)
    if not include_internal:
        q = q.filter_by(internal=False)
    return q.order_by(Comment.created_at, Comment.id).all()


# ------------------------------------------------------------------ helpers for routes
def paginate(query, per_page=20):
    page = request.args.get("page", 1, type=int)
    return query.paginate(page=page, per_page=per_page, error_out=False)


def csv_response(filename, header, rows):
    """CSV that opens cleanly in Excel (UTF-8 BOM) and is safe from formula injection."""
    def safe(cell):
        if cell is None:
            return ""
        text = str(cell)
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe(c) for c in row])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def lan_ip() -> str:
    """Best guess of this computer's address on the office network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ------------------------------------------------------------------ uploads
def save_upload(file_storage, subdir):
    """Save an uploaded file in the organised tree under instance/uploads (see storage.py).

    Returns (stored_path, original_name, size). ``stored_path`` is relative to the uploads folder and is what the
    database keeps. Any file type is accepted except programs and scripts (configurable in config.py).
    """
    from . import storage
    raw = (file_storage.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not raw:
        raise ValueError("Choose a file to upload.")
    clean = secure_filename(raw)
    raw_ext = raw.rsplit(".", 1)[-1].lower() if "." in raw else ""
    if not clean or (raw_ext and "." not in clean):       # names in Urdu/Arabic/etc. collapse to just the extension
        clean = f"file-{now():%Y%m%d-%H%M%S}" + (f".{raw_ext}" if raw_ext else "")
    ext = clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    if not storage.extension_allowed(ext):
        raise ValueError(f"Files of type .{ext} are blocked because they can run programs on someone's computer "
                         f"({storage.blocked_list_text()}). Put it inside a .zip file if you need to keep it.")
    rel_dir = storage.organized_dir(subdir)
    folder = storage.uploads_root() / rel_dir
    folder.mkdir(parents=True, exist_ok=True)
    stored = storage.stored_filename(clean)
    target = folder / stored
    file_storage.save(target)
    return f"{rel_dir}/{stored}", clean, target.stat().st_size


def upload_path(stored_name) -> Path:
    root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    path = (root / stored_name).resolve()
    if root not in path.parents:          # block path traversal
        raise ValueError("Invalid file path.")
    return path


def delete_upload(stored_name):
    try:
        p = upload_path(stored_name)
        if p.exists():
            p.unlink()
    except (ValueError, OSError):
        pass


# ------------------------------------------------------------------ backups
class BackupError(Exception):
    pass


def backup_dir() -> Path:
    d = Path(current_app.config["BACKUP_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backups():
    files = sorted(backup_dir().glob("hims_backup_*.zip"), reverse=True)
    return [{"name": f.name, "size": f.stat().st_size,
             "created": datetime.fromtimestamp(f.stat().st_mtime, TZ).replace(tzinfo=None)} for f in files]


def create_backup() -> Path:
    """Zip a consistent snapshot of the database plus the uploaded files."""
    url = db.engine.url
    stamp = now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir() / f"hims_backup_{stamp}.zip"
    upload_root = Path(current_app.config["UPLOAD_DIR"])

    with tempfile.TemporaryDirectory() as tmp:
        if url.get_backend_name() == "sqlite":
            snapshot, inner = Path(tmp) / "hims.db", "hims.db"
            src = sqlite3.connect(url.database)
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)            # safe even while other users are working
            finally:
                dst.close()
                src.close()
        else:
            snapshot, inner = Path(tmp) / "hims.sql", "hims.sql"
            env = {"PGPASSWORD": url.password or ""}
            cmd = ["pg_dump", "-h", url.host or "localhost", "-p", str(url.port or 5432),
                   "-U", url.username or "postgres", "--no-owner", "-f", str(snapshot), url.database]
            try:
                subprocess.run(cmd, check=True, capture_output=True, env={**os.environ, **env})
            except FileNotFoundError:
                raise BackupError("pg_dump was not found. Install PostgreSQL client tools or back up with your own scheduled pg_dump.")
            except subprocess.CalledProcessError as exc:
                raise BackupError("pg_dump failed: " + exc.stderr.decode(errors="ignore")[:300])

        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, inner)
            if upload_root.exists():
                for p in upload_root.rglob("*"):
                    if p.is_file():
                        z.write(p, "uploads/" + p.relative_to(upload_root).as_posix())
            z.writestr("MANIFEST.txt", f"Home Mate HIMS backup\nCreated: {now():%Y-%m-%d %H:%M:%S}\n"
                                       f"Database: {url.get_backend_name()}\nContents: {inner}, uploads/\n")

    _prune_backups()
    _copy_secondary(dest)
    return dest


def _prune_backups():
    keep = max(get_int("backup_keep", 14), 3)
    for old in list_backups()[keep:]:
        try:
            (backup_dir() / old["name"]).unlink()
        except OSError:
            pass


def _copy_secondary(dest: Path):
    target = (get_setting("backup_secondary_path", "") or "").strip()
    if not target:
        return
    folder = Path(target)
    if folder.is_dir():
        try:
            shutil.copy2(dest, folder / dest.name)
        except OSError:
            current_app.logger.warning("Could not copy backup to secondary location %s", folder)
    else:
        current_app.logger.warning("Secondary backup folder %s does not exist", folder)


def secondary_backup_status():
    target = (get_setting("backup_secondary_path", "") or "").strip()
    if not target:
        return "not set"
    return "reachable" if Path(target).is_dir() else "folder not found"


def verify_backup(path) -> dict:
    """Open a backup and check it is readable (SQLite backups: integrity check + row counts)."""
    path = Path(path)
    if not path.exists():
        raise BackupError(f"{path} does not exist.")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        result = {"file": path.name, "files_in_zip": len(names), "uploads": sum(n.startswith("uploads/") for n in names)}
        if "hims.db" in names:
            with tempfile.TemporaryDirectory() as tmp:
                z.extract("hims.db", tmp)
                conn = sqlite3.connect(Path(tmp) / "hims.db")
                try:
                    result["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
                    for table in ("users", "tasks", "departments"):
                        result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                finally:
                    conn.close()
        elif "hims.sql" in names:
            result["integrity"] = "PostgreSQL dump present (restore with psql to test)"
        else:
            raise BackupError("This zip does not look like a HIMS backup.")
    return result


def restore_backup(path):
    """Restore a SQLite backup over the live database. Stop HIMS before calling this."""
    url = db.engine.url
    if url.get_backend_name() != "sqlite":
        raise BackupError("Automatic restore supports SQLite only. For PostgreSQL restore hims.sql with psql.")
    info = verify_backup(path)
    if info.get("integrity") != "ok":
        raise BackupError("The backup failed its integrity check; it was not restored.")
    db_path = Path(url.database)
    db.session.remove()
    db.engine.dispose()
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_suffix(".db.before-restore"))
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    upload_root = Path(current_app.config["UPLOAD_DIR"])
    with zipfile.ZipFile(path) as z:
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("hims.db", tmp)
            shutil.copy2(Path(tmp) / "hims.db", db_path)
            for name in z.namelist():
                if name.startswith("uploads/") and not name.endswith("/"):
                    target = (upload_root / name[len("uploads/"):]).resolve()
                    if upload_root.resolve() in target.parents:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(name) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
    return info


def database_size() -> int:
    url = db.engine.url
    if url.get_backend_name() == "sqlite" and url.database:
        p = Path(url.database)
        return p.stat().st_size if p.exists() else 0
    return 0


def upload_limit_text():
    """e.g. '100 MB', for the help text under every file field."""
    return f"{current_app.config['MAX_UPLOAD_MB']} MB"


# Employee ID series. Each type is numbered on its own: HM-HOD-0001, HM-COH-0001, HM-EMP-0007, HM-INT-0002 ...
# An ID is permanent: promoting someone keeps the ID they were given when they joined.
EMPLOYEE_CODE_PREFIXES = {"super_admin": "ADM", "admin": "ADM", "department_head": "HOD", "co_head": "COH",
                          "team_lead": "TL", "employee": "EMP", "intern": "INT"}


def assign_employee_code(user):
    """Give a newly created (already flushed) user the next free ID in their type's series."""
    role = db.session.get(Role, user.role_id)
    prefix = f"HM-{EMPLOYEE_CODE_PREFIXES.get(role.key if role else None, 'EMP')}-"
    last = 0
    for (code,) in db.session.query(User.employee_code).filter(User.employee_code.like(prefix + "%")).all():
        try:
            last = max(last, int(code.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    user.employee_code = f"{prefix}{last + 1:04d}"
    return user.employee_code
