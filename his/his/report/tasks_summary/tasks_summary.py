# Copyright (c) 2026, Rasiin Tech and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart_data(data)
	report_summary = get_report_summary(data)
	return columns, data, None, chart, report_summary


def get_data(filters):
	conditions = get_conditions(filters)

	tasks = frappe.get_all(
		"Task",
		filters=conditions,
		fields=[
			"name",
			"subject",
			"department",
			"exp_start_date",
			"exp_end_date",
			"status",
			"priority",
			"completed_on",
			"progress",
		],
		order_by="department asc, exp_end_date asc, creation desc",
	)

	task_names = [d.name for d in tasks]
	assignment_user_map, assignment_fullname_map = get_all_task_assignments(task_names)

	filtered_tasks = []

	for task in tasks:
		task.delay = calculate_delay(task)
		task.department = task.department or "Not Assigned"

		assigned_user_ids = assignment_user_map.get(task.name, [])
		assigned_user_names = assignment_fullname_map.get(task.name, [])

		task.assigned_to = ", ".join(assigned_user_names)

		if filters.get("assigned_to"):
			if filters.get("assigned_to") not in assigned_user_ids:
				continue

		filtered_tasks.append(task)

	filtered_tasks.sort(key=lambda x: x.delay, reverse=True)
	return filtered_tasks


def get_all_task_assignments(task_names):
	if not task_names:
		return {}, {}

	rows = frappe.get_all(
		"Assignment Rule User",
		filters={
			"parent": ["in", task_names],
			"parenttype": "Task",
		},
		fields=["parent", "user"],
		order_by="idx asc",
	)

	user_ids = list({row.user for row in rows if row.user})

	user_full_name_map = {}
	if user_ids:
		users = frappe.get_all(
			"User",
			filters={"name": ["in", user_ids]},
			fields=["name", "full_name"],
		)
		user_full_name_map = {
			user.name: (user.full_name or user.name)
			for user in users
		}

	assignment_user_map = {}
	assignment_fullname_map = {}

	for row in rows:
		if row.parent not in assignment_user_map:
			assignment_user_map[row.parent] = []

		if row.parent not in assignment_fullname_map:
			assignment_fullname_map[row.parent] = []

		if row.user:
			assignment_user_map[row.parent].append(row.user)
			assignment_fullname_map[row.parent].append(
				user_full_name_map.get(row.user, row.user)
			)

	return assignment_user_map, assignment_fullname_map


def calculate_delay(task):
	if not task.exp_end_date:
		return 0

	if task.completed_on:
		return date_diff(task.completed_on, task.exp_end_date)

	if task.status == "Completed":
		return 0

	return date_diff(nowdate(), task.exp_end_date)


def get_conditions(filters):
	conditions = frappe._dict()

	if filters.get("priority"):
		conditions.priority = filters.get("priority")

	if filters.get("status"):
		conditions.status = filters.get("status")

	if filters.get("department"):
		conditions.department = filters.get("department")

	if filters.get("from_date"):
		conditions.exp_end_date = [">=", filters.get("from_date")]

	if filters.get("to_date"):
		conditions.exp_start_date = ["<=", filters.get("to_date")]

	return conditions


def get_chart_data(data):
	department_map = {}

	for row in data:
		dept = row.get("department") or "Not Assigned"

		if dept not in department_map:
			department_map[dept] = {"on_track": 0, "delayed": 0}

		if row.get("delay", 0) > 0:
			department_map[dept]["delayed"] += 1
		else:
			department_map[dept]["on_track"] += 1

	labels = list(department_map.keys())
	on_track_values = [department_map[d]["on_track"] for d in labels]
	delayed_values = [department_map[d]["delayed"] for d in labels]

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{
					"name": _("On Track"),
					"values": on_track_values,
				},
				{
					"name": _("Delayed"),
					"values": delayed_values,
				},
			],
		},
		"type": "bar",
		"colors": ["#2563eb", "#dc2626"],
		"barOptions": {
			"stacked": 0
		},
	}


def get_report_summary(data):
	total_tasks = len(data)
	on_track_tasks = sum(1 for row in data if row.get("delay", 0) <= 0)
	delayed_tasks = sum(1 for row in data if row.get("delay", 0) > 0)
	completed_tasks = sum(1 for row in data if row.get("status") == "Completed")
	total_departments = len(set((row.get("department") or "Not Assigned") for row in data))

	return [
		{
			"value": total_tasks,
			"label": _("Total Tasks"),
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"value": on_track_tasks,
			"label": _("On Track"),
			"datatype": "Int",
			"indicator": "Green",
		},
		{
			"value": delayed_tasks,
			"label": _("Delayed"),
			"datatype": "Int",
			"indicator": "Red",
		},
		{
			"value": completed_tasks,
			"label": _("Completed"),
			"datatype": "Int",
			"indicator": "Purple",
		},
		{
			"value": total_departments,
			"label": _("Departments"),
			"datatype": "Int",
			"indicator": "Orange",
		},
	]


def get_columns():
	return [
		{
			"fieldname": "name",
			"fieldtype": "Link",
			"label": _("Task"),
			"options": "Task",
			"width": 160,
		},
		{
			"fieldname": "subject",
			"fieldtype": "Data",
			"label": _("Subject"),
			"width": 220,
		},
		{
			"fieldname": "assigned_to",
			"fieldtype": "Data",
			"label": _("Assigned To"),
			"width": 240,
		},
		{
			"fieldname": "department",
			"fieldtype": "Link",
			"label": _("Department"),
			"options": "Department",
			"width": 150,
		},
		{
			"fieldname": "status",
			"fieldtype": "Data",
			"label": _("Status"),
			"width": 120,
		},
		{
			"fieldname": "priority",
			"fieldtype": "Data",
			"label": _("Priority"),
			"width": 100,
		},
		{
			"fieldname": "progress",
			"fieldtype": "Percent",
			"label": _("Progress"),
			"width": 110,
		},
		{
			"fieldname": "exp_start_date",
			"fieldtype": "Date",
			"label": _("Expected Start Date"),
			"width": 150,
		},
		{
			"fieldname": "exp_end_date",
			"fieldtype": "Date",
			"label": _("Expected End Date"),
			"width": 150,
		},
		{
			"fieldname": "completed_on",
			"fieldtype": "Date",
			"label": _("Completed On"),
			"width": 130,
		},
		{
			"fieldname": "delay",
			"fieldtype": "Int",
			"label": _("Delay (Days)"),
			"width": 110,
		},
	]