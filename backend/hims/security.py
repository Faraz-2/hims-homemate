"""Permission checks: role permission matrix + department gates."""
from functools import wraps

from flask import abort, current_app
from flask_login import current_user

from .constants import GATES


def has_perm(user, code: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_super:
        return True
    return code in user.permission_codes()


def dept_ok(user, gate: str) -> bool:
    """Admin-level users pass every gate; others must belong to a department that works in the module."""
    if not user or not user.is_authenticated:
        return False
    if user.is_admin_level:
        return True
    return user.dept_slug in GATES.get(gate, set())


def access(perm=None, gate=None):
    """Route decorator: signed in + (optional) permission + (optional) department gate."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if perm and not has_perm(current_user, perm):
                abort(403)
            if gate and not dept_ok(current_user, gate):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def can_view_department(user, department) -> bool:
    """Department workspaces are protected: members and admins only."""
    return bool(user.is_admin_level or (department and user.works_in(department.id)))


def is_mis_user(user) -> bool:
    """Operations (Excel & data analysts) and admins see company-wide numbers."""
    return bool(user.is_admin_level or user.dept_slug == "operations")


def is_pm_user(user) -> bool:
    """Who sees and coordinates every project company-wide: admins, the Project Management department, and
    anyone individually granted the projects.view_all right."""
    return bool(user.is_admin_level or user.dept_slug == "project-management" or has_perm(user, "projects.view_all"))


def can_view_reports(user) -> bool:
    return has_perm(user, "reports.view") or user.dept_slug == "operations"
