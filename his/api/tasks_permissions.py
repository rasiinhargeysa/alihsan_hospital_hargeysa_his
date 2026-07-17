import frappe

def task_query(user=None):
    user = user or frappe.session.user

    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return ""

    return f"""
        exists (
            select 1
            from `tabAssignment Rule User` aru
            where aru.parent = `tabTask`.name
              and aru.parenttype = 'Task'
              and aru.parentfield = 'assigned_to'
              and aru.user = {frappe.db.escape(user)}
        )
    """

def task_has_permission(doc, user=None, permission_type=None):
    user = user or frappe.session.user

    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return True

    for row in doc.assigned_to or []:
        if row.user == user:
            return True

    return False