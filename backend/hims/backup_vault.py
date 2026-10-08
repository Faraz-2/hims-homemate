"""Complete backups: department-wise, user-wise and date-wise.

How the pieces fit together
---------------------------
* ``services.create_backup()`` (unchanged) makes the **full backup**: one zip with the whole database and every uploaded
  file. That is the one you restore from.
* This module adds **organised backups**. One *run* (a daily automatic one, or one you start by hand) makes::

      instance/backups/organized/<year>/<date>/<time>/
          run.json                      what was backed up, when, by whom, how big
          by-department/<department>.zip   everything that belongs to one department
          by-user/<person>.zip             everything that belongs to one person

  so the folders themselves are the **date-wise** view, ``by-department`` is **department-wise**, ``by-user`` is
  **user-wise**. Each zip holds readable ``data/*.csv`` files (tasks, leave, expenses, vendors and their product lists,
  messages ... filtered to that department or person) plus the person's or department's uploaded ``files/`` sorted
  as ``files/<department>/<person>/<date>/``.
* Any administrator can also export one department, one person, or one date range on demand.

Organised backups are for finding, auditing and handing over a department's or person's data. To bring the whole system
back after a disaster, restore the full backup (``flask --app run restore``).
"""
import csv
import io
import json
import re
import shutil
import threading
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import Date, and_, false, or_, select

from .constants import GATES
from .extensions import db
from .models import (Comment, Department, Document, EmployeeDocument, Expense, InternalRequest, Message, StoredFile,
                     User, UserDepartmentRole)
from .services import BackupError, create_backup, get_int, get_setting, log_activity
from .storage import department_folder, person_folder, slug
from .timeutil import now

RUN_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_TIME = re.compile(r"^\d{6}$")
ZIP_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.zip$")
KINDS = ("by-department", "by-user")


# ------------------------------------------------------------------ locations
def organized_root() -> Path:
    d = Path(current_app.config["ORGANIZED_BACKUP_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ every uploaded file the database knows about
def file_catalog():
    """List of dicts (source, title, stored, original, user, dept, when) for every uploaded file, whatever table it
    is attached to and wherever it sits on disk (old flat folders or the new organised tree)."""
    rows = []

    def add(source, title, stored, original, user, dept, when):
        if stored:
            rows.append(dict(source=source, title=title or original, stored=stored, original=original or Path(stored).name,
                             user=user, dept=dept or (user.department if user is not None else None), when=when))

    for c in Comment.query.filter(Comment.attachment_file.isnot(None)).all():
        add(c.entity_type or "comments", (c.body or "")[:60], c.attachment_file, c.attachment_name, c.author, None, c.created_at)
    for d in Document.query.all():
        add("documents", d.title, d.file_name, d.original_name, d.uploaded_by, d.department, d.created_at)
    for e in EmployeeDocument.query.all():
        add("employee-documents", e.document_type.name if e.document_type else "", e.file_name, e.original_name, e.user, None, e.uploaded_at)
    for x in Expense.query.filter(Expense.receipt_file.isnot(None)).all():
        add("receipts", x.title, x.receipt_file, x.receipt_name, x.submitted_by, x.department, x.created_at)
    for m in Message.query.filter(Message.attachment_file.isnot(None)).all():
        add("messages", (m.body or "")[:60], m.attachment_file, m.attachment_name, m.sender, None, m.created_at)
    for r in InternalRequest.query.filter(InternalRequest.attachment_file.isnot(None)).all():
        add("requests", r.title, r.attachment_file, r.attachment_name, r.requester, r.from_department, r.created_at)
    for f in StoredFile.query.all():
        add("files" if not f.entity_type else f.entity_type + "-files", f.title, f.file_name, f.original_name, f.owner, f.department, f.created_at)
    return rows


# ------------------------------------------------------------------ what a backup covers
class Scope:
    """all | department | user, optionally limited to a date range."""

    def __init__(self, kind="all", department=None, user=None, start=None, end=None):
        self.kind, self.department, self.user, self.start, self.end = kind, department, user, start, end
        if kind == "department":
            ids = {u.id for u in department.members}
            ids |= {r.user_id for r in UserDepartmentRole.query.filter_by(department_id=department.id).all()}
            self.user_ids = ids
        elif kind == "user":
            self.user_ids = {user.id}
        else:
            self.user_ids = None

    @property
    def label(self):
        if self.kind == "department":
            return f"Department: {self.department.name}"
        if self.kind == "user":
            return f"Person: {self.user.name} ({self.user.employee_code or self.user.id})"
        return "Whole company"

    @property
    def file_stem(self):
        if self.kind == "department":
            return f"department_{self.department.slug}"
        if self.kind == "user":
            return f"person_{person_folder(self.user)}"
        return "company_all"

    def in_dates(self, when):
        if when is None or (self.start is None and self.end is None):
            return True
        d = when.date() if isinstance(when, datetime) else when
        return (self.start is None or d >= self.start) and (self.end is None or d <= self.end)

    def wants(self, entry):
        """Does a catalogued file belong in this backup?"""
        if not self.in_dates(entry["when"]):
            return False
        if self.kind == "department":
            return entry["dept"] is not None and entry["dept"].id == self.department.id
        if self.kind == "user":
            return entry["user"] is not None and entry["user"].id == self.user.id
        return True


# Columns that say "this row belongs to that person / department / date".
USER_COLS = ("user_id", "owner_id", "assignee_id", "created_by_id", "submitted_by_id", "requester_id",
             "requested_by_id", "uploaded_by_id", "sender_id", "manager_id", "changed_by_id")
DEPT_COLS = ("department_id", "from_department_id", "to_department_id", "target_department_id", "requesting_department_id")
DATE_COLS = ("created_at", "work_date", "expense_date", "uploaded_at", "changed_at", "start_date")
SKIP_TABLES = {"role_permissions"}
HIDE_COLUMNS = {"users": {"password_hash", "failed_logins", "locked_until"}}       # never export password hashes
CHILD_OF = {"task_checklist_items": ("task_id", "tasks"), "project_members": ("project_id", "projects"),
            "vendor_product_prices": ("product_id", "vendor_products")}
FIRST = ["users", "departments", "tasks", "projects", "campaigns", "vendors", "vendor_products", "leads", "candidates",
         "tickets", "internal_requests", "expenses", "conversations"]


def _in(column, values):
    values = list(values)
    if not values:
        return false()                                         # matches nothing
    chunks = [values[i:i + 500] for i in range(0, len(values), 500)]
    return or_(*[column.in_(c) for c in chunks])


def _date_filter(table, scope):
    if scope.start is None and scope.end is None:
        return None
    col = next((table.c[n] for n in DATE_COLS if n in table.c), None)
    if col is None:
        return None
    is_date = isinstance(col.type, Date)
    parts = []
    if scope.start is not None:
        parts.append(col >= (scope.start if is_date else datetime.combine(scope.start, datetime.min.time())))
    if scope.end is not None:
        parts.append(col <= (scope.end if is_date else datetime.combine(scope.end, datetime.max.time())))
    return and_(*parts)


def _scope_condition(table, scope, ids):
    """(include_table, condition). condition None means 'every row'."""
    if scope.kind == "all":
        return True, None
    name, c = table.name, table.c
    if name in CHILD_OF:
        fk, parent = CHILD_OF[name]
        return (parent in ids), _in(c[fk], ids.get(parent, ()))
    if scope.kind == "department" and scope.department.slug in GATES["vendors"] and name in ("vendors", "vendor_products", "vendor_product_prices"):
        return True, None                                   # a vendor-handling department backs up every vendor list
    if name == "departments":
        dept_ids = {scope.department.id} if scope.kind == "department" else \
            {d for d in [scope.user.department_id] + [r.department_id for r in scope.user.secondary_roles] if d}
        return True, _in(c.id, dept_ids)
    if name == "users":
        return True, _in(c.id, scope.user_ids)
    parts = []
    if scope.user_ids is not None:
        parts += [_in(c[n], scope.user_ids) for n in USER_COLS if n in c]
    if scope.kind == "department":
        parts += [c[n] == scope.department.id for n in DEPT_COLS if n in c]
    if name == "comments":                                  # comments on the department's tasks, whoever wrote them
        parts.append(and_(c.entity_type == "task", _in(c.entity_id, ids.get("tasks", ()))))
    return (True, or_(*parts)) if parts else (False, None)  # tables with no owner (roles, settings...) are company-wide only


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value                                  # stops spreadsheet programs running pasted formulas
    return value


def _table_order():
    tables = {t.name: t for t in db.metadata.sorted_tables}
    ordered = [tables[n] for n in FIRST if n in tables]
    return ordered + [t for n, t in tables.items() if n not in FIRST]


def build_scope_zip(dest: Path, scope: Scope) -> dict:
    """Write one organised backup zip for a scope and return a summary (counts, size, files, problems)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ids, counts = {}, {}
    uploads = Path(current_app.config["UPLOAD_DIR"]).resolve()
    used_names, missing, file_count, file_bytes = set(), [], 0, 0

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for table in _table_order():
            if table.name in SKIP_TABLES:
                continue
            include, condition = _scope_condition(table, scope, ids)
            if not include:
                continue
            hidden = HIDE_COLUMNS.get(table.name, set())
            columns = [col for col in table.c if col.name not in hidden]
            stmt = select(*columns)
            conds = [x for x in (condition, _date_filter(table, scope) if table.name not in ("users", "departments") else None) if x is not None]
            if conds:
                stmt = stmt.where(and_(*conds))
            rows = db.session.execute(stmt).all()
            if not rows and table.name not in ("users", "departments"):
                continue
            if "id" in table.c:
                ids[table.name] = {r._mapping["id"] for r in rows}
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([col.name for col in columns])
            for r in rows:
                writer.writerow([_cell(r._mapping[col.name]) for col in columns])
            z.writestr(f"data/{table.name}.csv", "\ufeff" + buf.getvalue())
            counts[table.name] = len(rows)

        for entry in file_catalog():
            if not scope.wants(entry):
                continue
            try:
                path = (uploads / entry["stored"]).resolve()
                if uploads not in path.parents or not path.is_file():
                    raise FileNotFoundError(entry["stored"])
            except (OSError, ValueError):
                missing.append(entry["stored"])
                continue
            when = entry["when"] or now()
            base = f"files/{department_folder(entry['user'], entry['dept'])}/{person_folder(entry['user'])}/{when:%Y-%m-%d}/"
            arc = base + f"{slug(entry['source'], 'file', 24)}__{entry['original']}"
            first, n = arc, 1
            while arc in used_names:
                n += 1
                stem, dot, ext = first.rpartition(".")
                arc = f"{stem}_{n}.{ext}" if dot and "/" not in ext else f"{first}_{n}"
            used_names.add(arc)
            z.write(path, arc)
            file_count += 1
            file_bytes += path.stat().st_size

        manifest = {
            "app": current_app.config["APP_NAME"], "version": current_app.config["VERSION"],
            "created": now().strftime("%Y-%m-%d %H:%M:%S"), "scope": scope.label,
            "from": scope.start.isoformat() if scope.start else None, "to": scope.end.isoformat() if scope.end else None,
            "tables": counts, "files": file_count, "file_bytes": file_bytes, "files_missing_on_disk": missing,
        }
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
        z.writestr("README.txt",
                   f"Home Mate HIMS - organised backup\n{scope.label}\nCreated: {manifest['created']}\n\n"
                   "data/    one CSV file per kind of record, filtered to this scope (open in Excel).\n"
                   "files/   uploaded files sorted by department, then person, then date.\n\n"
                   "This is a readable export for finding and handing over information. To restore the whole system use the\n"
                   "full backup (hims_backup_*.zip) with: flask --app run restore <file>\n"
                   "Password hashes are never included. Text cells starting with = + - @ are prefixed with ' so spreadsheets\n"
                   "cannot run them as formulas.\n")
    summary = dict(manifest)
    summary["size"] = dest.stat().st_size
    return summary


# ------------------------------------------------------------------ one complete run
def create_complete_backup(trigger="manual"):
    """Full backup + a zip per department + a zip per person, filed by date. Returns the run's manifest dict."""
    stamp = now()
    run_dir = organized_root() / f"{stamp:%Y}" / f"{stamp:%Y-%m-%d}" / f"{stamp:%H%M%S}"
    (run_dir / "by-department").mkdir(parents=True, exist_ok=True)
    (run_dir / "by-user").mkdir(parents=True, exist_ok=True)
    errors, departments, users = [], [], []

    full = None
    try:
        full = create_backup()
    except BackupError as exc:
        errors.append(f"Full backup: {exc}")

    for dept in Department.query.order_by(Department.name).all():
        try:
            info = build_scope_zip(run_dir / "by-department" / f"{dept.slug}.zip", Scope("department", department=dept))
            departments.append({"slug": dept.slug, "name": dept.name, "size": info["size"], "files": info["files"],
                                "rows": sum(info["tables"].values())})
        except Exception as exc:                            # one bad department must not lose the rest of the run
            errors.append(f"Department {dept.name}: {exc}")
            current_app.logger.exception("Department backup failed for %s", dept.slug)
    for user in User.query.order_by(User.name).all():
        try:
            fname = f"{person_folder(user)}.zip"
            info = build_scope_zip(run_dir / "by-user" / fname, Scope("user", user=user))
            users.append({"id": user.id, "name": user.name, "code": user.employee_code, "file": fname,
                          "department": user.department.name if user.department else "",
                          "size": info["size"], "files": info["files"], "rows": sum(info["tables"].values())})
        except Exception as exc:
            errors.append(f"Person {user.name}: {exc}")
            current_app.logger.exception("User backup failed for %s", user.id)

    manifest = {"created": stamp.strftime("%Y-%m-%d %H:%M:%S"), "trigger": trigger, "version": current_app.config["VERSION"],
                "full_backup": full.name if full else None, "full_size": full.stat().st_size if full else 0,
                "departments": departments, "users": users, "errors": errors}
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _prune_runs()
    _copy_run_secondary(run_dir)
    log_activity("backup", "system", None, f"Complete backup run {stamp:%Y-%m-%d %H:%M} ({trigger}): "
                                           f"{len(departments)} departments, {len(users)} people" + (f", {len(errors)} problem(s)" if errors else ""))
    db.session.commit()
    return manifest


def list_runs():
    """Newest first: [{date, time, path, manifest, size}]"""
    root = organized_root()
    runs = []
    for year in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        for day in sorted((p for p in year.iterdir() if p.is_dir() and RUN_DATE.match(p.name)), reverse=True):
            for run in sorted((p for p in day.iterdir() if p.is_dir() and RUN_TIME.match(p.name)), reverse=True):
                info_file = run / "run.json"
                try:
                    manifest = json.loads(info_file.read_text(encoding="utf-8")) if info_file.exists() else {}
                except (OSError, ValueError):
                    manifest = {}
                size = sum(f.stat().st_size for f in run.rglob("*") if f.is_file())
                runs.append({"date": day.name, "time": run.name, "label": f"{run.name[:2]}:{run.name[2:4]}", "path": run,
                             "manifest": manifest, "size": size})
    return runs


def run_file(day, run_time, kind, name) -> Path:
    """Safe path to one zip inside a run; raises ValueError for anything that does not look right."""
    if not (RUN_DATE.match(day) and RUN_TIME.match(run_time) and kind in KINDS and ZIP_NAME.match(name)):
        raise ValueError("Invalid backup path.")
    root = organized_root().resolve()
    path = (root / day[:4] / day / run_time / kind / name).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("That backup file does not exist.")
    return path


def user_history(user):
    """Every organised backup of one person across all dates: [{date, time, kind, name, size}]"""
    fname = f"{person_folder(user)}.zip"
    out = []
    for run in list_runs():
        p = run["path"] / "by-user" / fname
        if p.is_file():
            out.append({"date": run["date"], "time": run["time"], "label": run["label"], "name": fname, "size": p.stat().st_size})
    return out


def _prune_runs():
    keep = max(get_int("backup_keep_runs", 10), 2)
    for run in list_runs()[keep:]:
        shutil.rmtree(run["path"], ignore_errors=True)
        day = run["path"].parent
        if day.exists() and not any(day.iterdir()):
            day.rmdir()
            if not any(day.parent.iterdir()):
                day.parent.rmdir()


def _copy_run_secondary(run_dir: Path):
    target = (get_setting("backup_secondary_path", "") or "").strip()
    if not target or not Path(target).is_dir():
        return
    root = organized_root()
    try:
        shutil.copytree(run_dir, Path(target) / "organized" / run_dir.relative_to(root), dirs_exist_ok=True)
    except OSError:
        current_app.logger.warning("Could not copy the organised backup to %s", target)


# ------------------------------------------------------------------ automatic daily run
_started = False


def todays_run_exists():
    day = organized_root() / f"{now():%Y}" / f"{now():%Y-%m-%d}"
    return day.is_dir() and any(day.glob("*/run.json"))


def _due():
    if str(get_setting("auto_backup", "1")) != "1":
        return False
    try:
        hh, mm = (int(x) for x in str(get_setting("auto_backup_time", "22:00")).split(":"))
    except ValueError:
        hh, mm = 22, 0
    t = now()
    return (t.hour, t.minute) >= (hh, mm) and not todays_run_exists()


def _loop(app):
    time.sleep(90)                                          # let the server finish starting first
    while True:
        try:
            with app.app_context():
                if _due():
                    create_complete_backup(trigger="automatic")
        except Exception:                                   # never let the backup thread die
            app.logger.exception("Automatic backup failed")
        time.sleep(300)


def start_scheduler(app):
    """Called by run.py. Checks every 5 minutes whether today's complete backup is due, and makes it."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(app,), name="hims-auto-backup", daemon=True).start()


# ------------------------------------------------------------------ where everything is stored (for the Storage map page)
def _tree_size(path: Path):
    files = size = 0
    for f in path.rglob("*"):
        if f.is_file():
            files += 1
            size += f.stat().st_size
    return files, size


def storage_overview():
    cfg = current_app.config
    uploads = Path(cfg["UPLOAD_DIR"])
    depts = {d.slug: d for d in Department.query.all()}
    people = {person_folder(u): u for u in User.query.all()}
    tree, legacy = [], {"files": 0, "bytes": 0, "folders": []}
    if uploads.is_dir():
        root = uploads / "departments"
        if root.is_dir():
            for dept_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                dept = depts.get(dept_dir.name)
                row = {"name": dept.name if dept else ("Company-wide / no department" if dept_dir.name == "_company" else dept_dir.name),
                       "folder": f"departments/{dept_dir.name}", "files": 0, "bytes": 0, "people": []}
                for person_dir in sorted(p for p in dept_dir.iterdir() if p.is_dir()):
                    f, b = _tree_size(person_dir)
                    user = people.get(person_dir.name)
                    row["people"].append({"name": user.name if user else person_dir.name, "folder": person_dir.name,
                                          "code": user.employee_code if user else "", "files": f, "bytes": b})
                    row["files"] += f
                    row["bytes"] += b
                tree.append(row)
        for child in sorted(p for p in uploads.iterdir() if p.is_dir() and p.name != "departments"):
            f, b = _tree_size(child)
            legacy["folders"].append({"name": child.name, "files": f, "bytes": b})
            legacy["files"] += f
            legacy["bytes"] += b
    db_url = str(cfg["SQLALCHEMY_DATABASE_URI"])
    locations = [
        ("Database", db_url.split("///", 1)[-1] if db_url.startswith("sqlite") else "PostgreSQL (server setting)",
         "Every record: people, tasks, vendors and product lists, leave, expenses, messages."),
        ("Uploaded files", str(uploads), "Every attachment and document, in department / person / year / month folders."),
        ("Full backups", str(cfg["BACKUP_DIR"]), "hims_backup_<date>.zip: database plus every uploaded file. Restore from these."),
        ("Organised backups", str(cfg["ORGANIZED_BACKUP_DIR"]), "<year>/<date>/<time>/by-department and by-user zips."),
        ("Backend (code)", str(Path(__file__).resolve().parent.parent), "All Python code. Safe to replace on upgrade."),
        ("Frontend (pages and styling)", str(cfg["FRONTEND_DIR"]), "Templates and static files. Safe to replace on upgrade."),
    ]
    return {"locations": locations, "tree": tree, "legacy": legacy}
