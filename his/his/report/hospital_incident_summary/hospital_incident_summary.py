# Copyright (c) 2026, HIS Module
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, today, date_diff


def execute(filters=None):
	filters = frappe._dict(filters or {})

	columns = get_columns()
	data = get_data(filters)
	chart = get_chart_data(data)
	report_summary = get_report_summary(data)

	return columns, data, None, chart, report_summary


def get_columns():
	return [
		{
			"label": _("Incident"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Hospital Incident",
			"width": 110,
		},
		{
			"label": _("Title"),
			"fieldname": "incident_title",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Incident Date"),
			"fieldname": "incident_date",
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"label": _("Type"),
			"fieldname": "incident_type",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Severity"),
			"fieldname": "severity",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Department"),
			"fieldname": "department",
			"fieldtype": "Link",
			"options": "Department",
			"width": 140,
		},
		{
			"label": _("Location"),
			"fieldname": "location",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Reported By"),
			"fieldname": "reported_by",
			"fieldtype": "Link",
			"options": "User",
			"width": 130,
		},
		{
			"label": _("Investigator"),
			"fieldname": "investigator",
			"fieldtype": "Link",
			"options": "User",
			"width": 130,
		},
		{
			"label": _("Assigned To"),
			"fieldname": "assigned_to",
			"fieldtype": "Link",
			"options": "User",
			"width": 130,
		},
		{
			"label": _("Due Date"),
			"fieldname": "due_date",
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"label": _("Action Status"),
			"fieldname": "action_status",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Days Open"),
			"fieldname": "days_open",
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"label": _("Overdue"),
			"fieldname": "overdue",
			"fieldtype": "Data",
			"width": 80,
		},
	]


def get_conditions(filters):
	conditions = {}

	if filters.get("company"):
		# placeholder for multi-facility setups; remove if not applicable
		pass

	if filters.get("department"):
		conditions["department"] = filters.department

	if filters.get("incident_type"):
		conditions["incident_type"] = filters.incident_type

	if filters.get("severity"):
		conditions["severity"] = filters.severity

	if filters.get("status"):
		conditions["status"] = filters.status

	if filters.get("action_status"):
		conditions["action_status"] = filters.action_status

	if filters.get("investigator"):
		conditions["investigator"] = filters.investigator

	if filters.get("assigned_to"):
		conditions["assigned_to"] = filters.assigned_to

	if filters.get("from_date") and filters.get("to_date"):
		conditions["incident_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.get("from_date"):
		conditions["incident_date"] = [">=", filters.from_date]
	elif filters.get("to_date"):
		conditions["incident_date"] = ["<=", filters.to_date]

	return conditions


def get_data(filters):
	conditions = get_conditions(filters)

	rows = frappe.get_all(
		"Hospital Incident",
		filters=conditions,
		fields=[
			"name",
			"incident_title",
			"incident_date",
			"incident_type",
			"severity",
			"status",
			"department",
			"location",
			"reported_by",
			"investigator",
			"assigned_to",
			"due_date",
			"action_status",
			"completion_date",
		],
		order_by="incident_date desc",
	)

	for row in rows:
		row["days_open"] = get_days_open(row)
		row["overdue"] = get_overdue_flag(row)

	return rows


def get_days_open(row):
	"""Number of days the incident has been open (or was open until closure)."""
	start = getdate(row.get("incident_date")) if row.get("incident_date") else None
	if not start:
		return 0

	if row.get("status") == "Closed" and row.get("completion_date"):
		end = getdate(row.get("completion_date"))
	else:
		end = getdate(today())

	return date_diff(end, start)


def get_overdue_flag(row):
	"""Flags an incident whose corrective action is past due and not completed."""
	if (
		row.get("due_date")
		and row.get("action_status") != "Completed"
		and row.get("status") != "Closed"
		and getdate(row.get("due_date")) < getdate(today())
	):
		return _("Yes")
	return ""


def get_chart_data(data):
	if not data:
		return None

	status_counts = {}
	for row in data:
		status = row.get("status") or _("Not Set")
		status_counts[status] = status_counts.get(status, 0) + 1

	return {
		"data": {
			"labels": list(status_counts.keys()),
			"datasets": [{"name": _("Incidents"), "values": list(status_counts.values())}],
		},
		"type": "donut",
		"colors": ["#5e64ff", "#ff5858", "#ffa00a", "#28a745", "#7cd6fd"],
	}


def get_report_summary(data):
	if not data:
		return []

	total = len(data)
	open_count = len([d for d in data if d.get("status") != "Closed"])
	critical_count = len([d for d in data if d.get("severity") == "Critical"])
	overdue_count = len([d for d in data if d.get("overdue") == _("Yes")])

	return [
		{
			"value": total,
			"label": _("Total Incidents"),
			"datatype": "Int",
		},
		{
			"value": open_count,
			"label": _("Open / In Progress"),
			"datatype": "Int",
			"indicator": "Orange" if open_count else "Green",
		},
		{
			"value": critical_count,
			"label": _("Critical Severity"),
			"datatype": "Int",
			"indicator": "Red" if critical_count else "Green",
		},
		{
			"value": overdue_count,
			"label": _("Overdue Actions"),
			"datatype": "Int",
			"indicator": "Red" if overdue_count else "Green",
		},
	]