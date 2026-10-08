"""Small status-workflow engine shared by tasks and design/content requests."""
from .security import has_perm


def actor_roles(obj, user, manager_perm=None):
    """Which hats does this user wear for this record? assignee / creator / manager."""
    roles = set()
    if obj.assignee_id == user.id:
        roles.add("assignee")
    if obj.owner_id == user.id:
        roles.add("creator")
    is_manager = user.manages_department(obj.department_id)
    if is_manager and manager_perm and not user.is_admin_level:
        is_manager = has_perm(user, manager_perm)
    if is_manager:
        roles.add("manager")
    return roles


def available(flow, obj, user, manager_perm=None):
    roles = actor_roles(obj, user, manager_perm)
    return [t for t in flow if obj.status in t.src and roles & t.who]


def find(flow, obj, dst, user, manager_perm=None):
    for t in available(flow, obj, user, manager_perm):
        if t.dst == dst:
            return t
    return None
