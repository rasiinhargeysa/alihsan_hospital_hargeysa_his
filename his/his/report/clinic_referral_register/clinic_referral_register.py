# Copyright (c) 2026, HIS
# Report: Clinic Referral Register
# Path  : his/his/report/clinic_referral_register/clinic_referral_register.py
#
# Shows every field of the Referral Form For Clinics doctype,
# plus a computed Net Amount column.

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate(filters)

	data = get_data(filters)
	columns = get_columns()
	chart = get_chart(data)
	return columns, data, None, chart, None


def _validate(filters):
	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be on or before To Date."))


def get_columns():
	return [
		{
			"fieldname": "name",
			"label": _("Referral"),
			"fieldtype": "Link",
			"options": "Referral Form For Clinics",
			"width": 140,
		},
		{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "priority", "label": _("Priority"), "fieldtype": "Data", "width": 100},
		# --- patient -----------------------------------------------------
		{"fieldname": "pid", "label": _("PID"), "fieldtype": "Data", "width": 90},
		{"fieldname": "patient_name", "label": _("Patient Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "age", "label": _("Age"), "fieldtype": "Data", "width": 60},
		{
			"fieldname": "sex",
			"label": _("Sex"),
			"fieldtype": "Link",
			"options": "Gender",
			"width": 80,
		},
		{"fieldname": "mobile", "label": _("Mobile"), "fieldtype": "Data", "width": 120},
		{"fieldname": "address", "label": _("Address"), "fieldtype": "Data", "width": 180},
		# --- emergency contact -------------------------------------------
		{
			"fieldname": "emergency_contact_name",
			"label": _("Emergency Contact Name"),
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"fieldname": "emergency_contact_number",
			"label": _("Emergency Contact Number"),
			"fieldtype": "Data",
			"width": 170,
		},
		# --- referral ----------------------------------------------------
		{"fieldname": "department", "label": _("Department"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "reason_for_refferal",
			"label": _("Reason For Referral"),
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"fieldname": "requested_speciality",
			"label": _("Requested Speciality"),
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"fieldname": "proposed_procedure",
			"label": _("Proposed Procedure"),
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"fieldname": "referred__clinic",
			"label": _("Referred Clinic"),
			"fieldtype": "Data",
			"width": 210,
		},
		# --- money -------------------------------------------------------
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "discount", "label": _("Discount"), "fieldtype": "Currency", "width": 100},
		{
			"fieldname": "net_amount",
			"label": _("Net Amount"),
			"fieldtype": "Currency",
			"width": 120,
		},
		# --- audit -------------------------------------------------------
		{
			"fieldname": "user",
			"label": _("User"),
			"fieldtype": "Link",
			"options": "User",
			"width": 170,
		},
		{"fieldname": "username", "label": _("Username"), "fieldtype": "Data", "width": 150},
	]


def get_data(filters):
	conditions, values = get_conditions(filters)

	return frappe.db.sql(
		f"""
		SELECT
			r.name,
			r.date,
			r.priority,
			r.pid,
			r.patient_name,
			r.age,
			r.sex,
			r.mobile,
			r.address,
			r.emergency_contact_name,
			r.emergency_contact_number,
			r.department,
			r.reason_for_refferal,
			r.requested_speciality,
			r.proposed_procedure,
			r.referred__clinic,
			r.amount,
			r.discount,
			(IFNULL(r.amount, 0) - IFNULL(r.discount, 0)) AS net_amount,
			r.user,
			r.username
		FROM `tabReferral Form For Clinics` r
		WHERE {conditions}
		ORDER BY r.date DESC, r.creation DESC
		""",
		values,
		as_dict=True,
	)


def get_conditions(filters):
	conditions = ["1 = 1"]
	values = {}

	if filters.from_date:
		conditions.append("r.date >= %(from_date)s")
		values["from_date"] = filters.from_date

	if filters.to_date:
		conditions.append("r.date <= %(to_date)s")
		values["to_date"] = filters.to_date

	if filters.referred__clinic:
		conditions.append("r.referred__clinic = %(referred__clinic)s")
		values["referred__clinic"] = filters.referred__clinic

	if filters.priority:
		conditions.append("r.priority = %(priority)s")
		values["priority"] = filters.priority

	if filters.department:
		conditions.append("r.department LIKE %(department)s")
		values["department"] = f"%{filters.department}%"

	if filters.requested_speciality:
		conditions.append("r.requested_speciality LIKE %(requested_speciality)s")
		values["requested_speciality"] = f"%{filters.requested_speciality}%"

	if filters.sex:
		conditions.append("r.sex = %(sex)s")
		values["sex"] = filters.sex

	if filters.patient:
		conditions.append("(r.patient_name LIKE %(patient)s OR r.pid LIKE %(patient)s)")
		values["patient"] = f"%{filters.patient}%"

	if filters.user:
		conditions.append("r.user = %(user)s")
		values["user"] = filters.user

	return " AND ".join(conditions), values


def get_chart(data):
	"""Net amount per receiving clinic."""
	totals = {}
	for row in data:
		clinic = row.get("referred__clinic") or _("Unassigned")
		totals[clinic] = totals.get(clinic, 0) + (row.get("net_amount") or 0)

	if not totals:
		return None

	ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)

	return {
		"data": {
			"labels": [clinic for clinic, _total in ordered],
			"datasets": [{"name": _("Net Amount"), "values": [total for _clinic, total in ordered]}],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#1f6b57"],
	}


def get_summary_cards(data):
	"""Card strip rendered above the table (returned as the report `message`)."""
	currency = frappe.db.get_default("currency") or ""

	def money(value):
		return frappe.utils.fmt_money(value, currency=currency)

	total = len(data)
	gross = sum((row.get("amount") or 0) for row in data)
	discount = sum((row.get("discount") or 0) for row in data)
	net = gross - discount

	emergency = sum(1 for row in data if row.get("priority") == "Emergency")
	urgent = sum(1 for row in data if row.get("priority") == "Urgent")
	elective = sum(1 for row in data if row.get("priority") == "Elective")

	male = sum(1 for row in data if (row.get("sex") or "").lower().startswith("m"))
	female = sum(1 for row in data if (row.get("sex") or "").lower().startswith("f"))

	cards = [
		{"icon": "&#128203;", "label": _("Total Referrals"), "value": total, "color": "#2d5fd1"},
		{"icon": "&#128680;", "label": _("Emergency"), "value": emergency, "color": "#d13a3a"},
		{"icon": "&#9203;", "label": _("Urgent"), "value": urgent, "color": "#d98014"},
		{"icon": "&#9989;", "label": _("Elective"), "value": elective, "color": "#1f9d63"},
		# {"icon": "&#128176;", "label": _("Gross Amount"), "value": money(gross), "color": "#0f766e"},
		{"icon": "&#127991;", "label": _("Discount"), "value": money(discount), "color": "#c2410c"},
		{"icon": "&#128202;", "label": _("Net Amount"), "value": money(net), "color": "#6d28d9"},
		{"icon": "&#128104;", "label": _("Gender: Male"), "value": male, "color": "#be185d"},
		{"icon": "&#128105;", "label": _("Gender: Female"), "value": female, "color": "#be185d"},
	]

	html = [
		'<div style="display:flex;flex-wrap:wrap;gap:12px;'
		'margin:2px 0 14px 0;font-family:inherit">'
	]

	for card in cards:
		html.append(
			'<div style="flex:1 1 150px;min-width:150px;'
			'background:var(--card-bg, #ffffff);'
			'border:1px solid var(--border-color, #e5e7eb);'
			'border-top:3px solid {color};'
			'border-radius:8px;padding:12px 14px;'
			'box-shadow:0 1px 2px rgba(0,0,0,0.04)">'
			'<div style="display:flex;align-items:center;gap:7px;'
			'font-size:12px;color:var(--text-muted, #6b7280);'
			'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
			'<span style="font-size:15px;line-height:1">{icon}</span>{label}</div>'
			'<div style="font-size:22px;font-weight:600;color:{color};'
			'margin-top:6px;line-height:1.2">{value}</div>'
			"</div>".format(
				color=card["color"],
				icon=card["icon"],
				label=frappe.utils.escape_html(str(card["label"])),
				value=frappe.utils.escape_html(str(card["value"])),
			)
		)

	html.append("</div>")
	return "".join(html)