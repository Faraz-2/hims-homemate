"""HIMS configuration.

Everything can be overridden with environment variables, so the same code runs on
the Phase 1 server laptop (SQLite) and later on a proper server (PostgreSQL).
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

# Project layout (since 1.5):
#   <root>/backend/   all Python code (this file, run.py, the hims package)
#   <root>/frontend/  everything the browser shows (templates and static files)
#   <root>/instance/  your data: database, uploads, backups, secret key (never moved, never overwritten by upgrades)
BASE_DIR = Path(__file__).resolve().parent            # the backend folder
ROOT_DIR = BASE_DIR.parent                            # the project folder
FRONTEND_DIR = Path(os.environ.get("HIMS_FRONTEND_DIR", ROOT_DIR / "frontend")).resolve()
INSTANCE_DIR = Path(os.environ.get("HIMS_INSTANCE_DIR", ROOT_DIR / "instance")).resolve()
UPLOAD_DIR = INSTANCE_DIR / "uploads"
BACKUP_DIR = INSTANCE_DIR / "backups"


def _secret_key() -> str:
    """Use HIMS_SECRET_KEY if set, otherwise create one once and keep it on disk."""
    key = os.environ.get("HIMS_SECRET_KEY")
    if key:
        return key
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    key_file = INSTANCE_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


def _database_uri() -> str:
    url = os.environ.get("HIMS_DATABASE_URL", "").strip()
    if url:
        # Some hosts still hand out postgres:// which SQLAlchemy no longer accepts.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + (INSTANCE_DIR / "hims.db").as_posix()


class Config:
    APP_NAME = "Home Mate HIMS"
    VERSION = "1.5.3"

    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessions: idle timeout (the cookie is refreshed on every request).
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("HIMS_SESSION_HOURS", "8")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("HIMS_HTTPS", "0") == "1"

    WTF_CSRF_TIME_LIMIT = None          # forms stay valid while a page is open all day
    MAX_UPLOAD_MB = int(os.environ.get("HIMS_MAX_UPLOAD_MB", "100"))   # videos need more room than documents
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    FRONTEND_DIR = FRONTEND_DIR
    TEMPLATE_DIR = FRONTEND_DIR / "templates"
    STATIC_DIR = FRONTEND_DIR / "static"
    UPLOAD_DIR = UPLOAD_DIR
    BACKUP_DIR = BACKUP_DIR
    # Organised backups (department-wise / user-wise / date-wise) live here, inside the backups folder.
    ORGANIZED_BACKUP_DIR = BACKUP_DIR / "organized"

    # Uploads: any file type is accepted. Programs and scripts that run when opened are blocked by default;
    # set HIMS_BLOCKED_EXTENSIONS to a different comma-separated list (or to "none") to change that.
    ALLOW_ANY_FILE_TYPE = os.environ.get("HIMS_ALLOW_ANY_FILE_TYPE", "1") == "1"
    BLOCKED_EXTENSIONS = (set() if os.environ.get("HIMS_BLOCKED_EXTENSIONS", "").strip().lower() == "none" else
                          {e.strip().lower().lstrip(".") for e in os.environ.get(
                              "HIMS_BLOCKED_EXTENSIONS",
                              "exe,bat,cmd,com,scr,msi,vbs,vbe,ps1,jar,dll,hta,lnk,pif").split(",") if e.strip()})
    AUTO_INIT = os.environ.get("HIMS_AUTO_INIT", "1") == "1"

    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 15
    MIN_PASSWORD_LENGTH = 8

    DEFAULT_ADMIN_EMAIL = os.environ.get("HIMS_ADMIN_EMAIL", "admin@homemate.local")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("HIMS_ADMIN_PASSWORD", "HomeMate@2026")
