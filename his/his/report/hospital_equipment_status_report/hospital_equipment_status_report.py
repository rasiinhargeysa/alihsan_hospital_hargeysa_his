# Copyright (c) 2026, Your Organization and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, flt


# Central place to control status colors across summary + chart.
# Change here once and it updates everywhere.
STATUS_COLOR_MAP = {
	"Not Requested": {"hex": "#e74c3c", "indicator": "red"},
	"Requested": {"hex": "#3498db", "indicator": "blue"},
	"Pending Delivery": {"hex": "#f1c40f", "indicator": "yellow"},
	"Delivered": {"hex": "#2ecc71", "indicator": "green"},
	"In Progress": {"hex": "#9b59b6", "indicator": "purple"},
	"Other": {"hex": "#95a5a6", "indicator": "grey"},
}

STATUS_ORDER = [
	"Not Requested",
	"Requested",
	"Pending Delivery",
	"Delivered",
	"In Progress",
	"Other",
]


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	summary = get_summary(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{
			"label": _("Date"),
			"fieldname": "date",
			"fieldtype": "Date",
			"width": 95,
		},
		{
			"label": _("Submitted By"),
			"fieldname": "full_name",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("Equipment"),
			"fieldname": "equipment",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Department"),
			"fieldname": "department",
			"fieldtype": "Link",
			"options": "Department",
			"width": 150,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 140,
		},
		{
			"label": _("Purchase Date"),
			"fieldname": "purchase_date",
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"label": _("Expected Delivery"),
			"fieldname": "expected_delivery",
			"fieldtype": "Date",
			"width": 130,
		},
		{
			"label": _("Cost"),
			"fieldname": "cost",
			"fieldtype": "Currency",
			"width": 110,
		},
		{
			"label": _("Remarks"),
			"fieldname": "remarks",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Parent Record"),
			"fieldname": "parent",
			"fieldtype": "Link",
			"options": "Hospital Equipment Status",
			"width": 150,
		},
	]


def get_conditions(filters):
	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("hes.date >= %(from_date)s")
		values["from_date"] = getdate(filters.get("from_date"))

	if filters.get("to_date"):
		conditions.append("hes.date <= %(to_date)s")
		values["to_date"] = getdate(filters.get("to_date"))

	if filters.get("user"):
		conditions.append("hes.user = %(user)s")
		values["user"] = filters.get("user")

	if filters.get("department"):
		conditions.append("hesi.department = %(department)s")
		values["department"] = filters.get("department")

	if filters.get("status"):
		conditions.append("hesi.status = %(status)s")
		values["status"] = filters.get("status")

	if filters.get("supplier"):
		conditions.append("hesi.supplier = %(supplier)s")
		values["supplier"] = filters.get("supplier")

	if filters.get("equipment"):
		conditions.append("hesi.equipment like %(equipment)s")
		values["equipment"] = "%{0}%".format(filters.get("equipment"))

	condition_str = " and {0}".format(" and ".join(conditions)) if conditions else ""
	return condition_str, values


def get_data(filters):
	condition_str, values = get_conditions(filters)

	data = frappe.db.sql(
		"""
		select
			hes.name as parent,
			hes.date as date,
			hes.full_name as full_name,
			hesi.equipment as equipment,
			hesi.department as department,
			hesi.status as status,
			hesi.supplier as supplier,
			hesi.purchase_date as purchase_date,
			hesi.expected_delivery as expected_delivery,
			hesi.cost as cost,
			hesi.remarks as remarks
		from
			`tabHospital Equipment Status` hes
		inner join
			`tabHospital Equipment Status Item` hesi
			on hesi.parent = hes.name
		where
			1 = 1 {condition_str}
		order by
			hes.date desc, hes.name desc
		""".format(
			condition_str=condition_str
		),
		values,
		as_dict=1,
	)

	return data


def get_summary(data):
	total = len(data)
	total_cost = sum(flt(d.get("cost")) for d in data)

	counts = {s: 0 for s in STATUS_ORDER}
	for d in data:
		s = d.get("status")
		if s in counts:
			counts[s] += 1

	summary = [
		{
			"value": total,
			"label": _("Total Equipment Entries"),
			"datatype": "Int",
			"indicator": "blue",
		},
	]

	for s in STATUS_ORDER:
		summary.append(
			{
				"value": counts[s],
				"label": _(s),
				"datatype": "Int",
				"indicator": STATUS_COLOR_MAP.get(s, {}).get("indicator", "grey"),
			}
		)

	summary.append(
		{
			"value": total_cost,
			"label": _("Total Cost"),
			"datatype": "Currency",
			"indicator": "green",
		}
	)

	return summary


def get_chart(data):
	"""Stacked bar chart: departments on the x-axis, one series per status,
	colored using STATUS_COLOR_MAP so the dashboard reads consistently with
	the summary cards (Not Requested = red, Requested = blue,
	Pending Delivery = yellow, Delivered = green)."""

	# Preserve first-seen department order for a stable, readable axis.
	departments = []
	for row in data:
		dept = row.get("department") or _("Not Set")
		if dept not in departments:
			departments.append(dept)

	# dept -> status -> count
	dept_status_counts = {dept: {s: 0 for s in STATUS_ORDER} for dept in departments}
	for row in data:
		dept = row.get("department") or _("Not Set")
		status = row.get("status")
		if status not in dept_status_counts[dept]:
			dept_status_counts[dept][status] = 0
		dept_status_counts[dept][status] = dept_status_counts[dept].get(status, 0) + 1

	datasets = []
	colors = []
	for status in STATUS_ORDER:
		values = [dept_status_counts[dept].get(status, 0) for dept in departments]
		# Skip a status series entirely if it never occurs, keeps the chart clean
		if not any(values):
			continue
		datasets.append({"name": _(status), "values": values})
		colors.append(STATUS_COLOR_MAP.get(status, {}).get("hex", "#95a5a6"))

	return {
		"data": {
			"labels": departments,
			"datasets": datasets,
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": colors,
		"axisOptions": {"xAxisMode": "tick", "xIsSeries": 0},
		"height": 280,
	}