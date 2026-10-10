# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Lab Result Analytics — Blood
============================
Everything server-side in one file: the Script Report, a schema inspector, the
dashboard builder, and the custom breakdown chart sources.

Base doctype : Lab Result          (parent)  — restricted to type == "Blood"
Child table  : Normal Test Result  (parenttype = "Lab Result")


ROW GRAIN — read this before changing anything
----------------------------------------------
`lab_test_name` and `result_value` live on the CHILD table, so one Lab Result
document holds many tests. This report returns ONE ROW PER ANALYTE, joined to
its parent document.

That distinction drives two different denominators, and mixing them up gives
wrong numbers:

  * Volume, abnormal rate, top tests  -> counted over ANALYTE rows
  * Document counts, patient/doctor/
    department/gender/age breakdowns  -> counted over DISTINCT PARENTS where
                                          the metric belongs to the document
                                          (e.g. one patient per document), or
                                          over analyte rows where the metric
                                          is a result (normal/abnormal mix).


QUICK START
-----------
1. See what resolved against your schema:

       bench --site your-site.local execute \
           your_app.healthcare.report.lab_result_analytics.lab_result_analytics.inspect

2. Fix any "!! UNRESOLVED" line by putting your real fieldname first in that
   role's candidate list in FIELD_MAP / CHILD_FIELD_MAP below.

3. Build the dashboard:

       bench --site your-site.local execute \
           your_app.healthcare.report.lab_result_analytics.lab_result_analytics.setup_dashboard
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, time_diff_in_hours

PARENT_DOCTYPE = "Lab Result"
CHILD_DOCTYPE = "Normal Test Result"

REPORT_NAME = "Lab Result Analytics"
DASHBOARD_NAME = "Laboratory Analytics"

# Frappe adds these to every doctype; `meta.has_field` does not report them.
STANDARD_FIELDS = {"name", "creation", "modified", "owner", "modified_by", "docstatus", "idx"}


# ==========================================================================
# THE ONLY BLOCK YOU SHOULD NEED TO EDIT
# ==========================================================================

# Hard restriction: only Lab Results of this type are ever read.
# Set to None to read every type.
RESULT_TYPE = "Blood"

FIELD_MAP = {
	# role              candidate fieldnames, best first
	# The type field carrying "Blood". If this cannot be resolved the report
	# refuses to run rather than silently returning every result type.
	"type": ["type", "lab_result_type", "result_type", "sample_type", "category"],
	"patient": ["patient", "patient_id"],
	"patient_name": ["patient_name", "patient_full_name"],
	"patient_age": ["patient_age", "age", "p_age"],
	# Unit the age above is expressed in — Day / Month / Year.
	"patient_age_type": ["patient_age_type", "age_type", "age_unit", "p_age_type"],
	# NOTE: gender is intentionally NOT resolved here. It is read from the
	# Patient master (see PATIENT_FIELD_MAP below) via a join on this role,
	# not from a gender field on Lab Result itself — the master record is the
	# source of truth and Lab Result copies can go stale or be left blank.
	"practitioner": ["practitioner", "ordering_practitioner", "referring_practitioner", "physician"],
	"department": ["department", "medical_department"],
	"template": ["template", "lab_test_template", "test_template", "lab_test_code"],
	"section": ["lab_test_group", "lab_section", "test_group", "section"],
	"status": ["status", "workflow_state", "result_status"],
	"technician": ["employee_name", "technician_name", "employee", "technician", "performed_by"],
	"company": ["company"],
	"invoiced": ["invoiced", "is_billed"],
	"amount": ["lab_test_rate", "amount", "rate", "paid_amount"],
	# Used only to place a row/document on the Daily / Weekly / Monthly /
	# Quarterly / Yearly comparison chart.
	"ordered_at": ["order_date", "requested_date", "sample_collection_time", "creation"],
}

# The child table. `test_name` and `result_value` are required — without them
# there is nothing to analyse.
CHILD_FIELD_MAP = {
	"test_name": ["lab_test_name", "test_name", "parameter", "analyte"],
	"result_value": ["result_value", "result", "value"],
	"uom": ["lab_test_uom", "uom", "unit"],
	# Free-text range, e.g. "3.5 - 5.0". Used when min/max are absent.
	"normal_range": ["normal_range", "reference_range", "range"],
	# Numeric bounds, if your build has them. Preferred — exact beats parsing.
	"min_value": ["min_normal_value", "min_value", "normal_min", "lower_limit"],
	"max_value": ["max_normal_value", "max_value", "normal_max", "upper_limit"],
}

PATIENT_DOCTYPE = "Patient"

# Gender is read from the Patient master, not from Lab Result. Resolved
# against PATIENT_DOCTYPE and joined in via the "patient" role above.
PATIENT_FIELD_MAP = {
	"patient_sex": ["sex", "gender", "patient_sex"],
}

# Only Lab Results at this docstatus are ever read (1 = Submitted). Drafts
# (0) and cancelled documents (2) are excluded everywhere: the report query,
# the dashboard charts, and the dashboard number cards.
DOCSTATUS = 1

# Status values that count as a finalised result. Run inspect() to see yours.
COMPLETED_STATUSES = ["Completed", "Approved", "Verified", "Released", "Final"]


# ==========================================================================
# Schema resolution
# ==========================================================================


def resolve_fields(doctype, field_map):
	"""Return {role: fieldname} for roles that exist on this doctype."""
	if not frappe.db.exists("DocType", doctype):
		return {}

	meta = frappe.get_meta(doctype)
	resolved = {}
	for role, candidates in field_map.items():
		for candidate in candidates:
			if candidate in STANDARD_FIELDS or meta.has_field(candidate):
				resolved[role] = candidate
				break
	return resolved


def get_schema():
	return {
		"parent": resolve_fields(PARENT_DOCTYPE, FIELD_MAP),
		"child": resolve_fields(CHILD_DOCTYPE, CHILD_FIELD_MAP),
		"patient": resolve_fields(PATIENT_DOCTYPE, PATIENT_FIELD_MAP),
	}


def inspect():
	"""Print what resolved on this site. Run this first."""
	schema = get_schema()

	for scope, doctype, field_map in (
		("parent", PARENT_DOCTYPE, FIELD_MAP),
		("child", CHILD_DOCTYPE, CHILD_FIELD_MAP),
		("patient", PATIENT_DOCTYPE, PATIENT_FIELD_MAP),
	):
		print("\n=== {0} ({1}) ===".format(doctype, scope))
		if not frappe.db.exists("DocType", doctype):
			print("  DOCTYPE NOT FOUND")
			continue
		for role in field_map:
			print("  {0:<18} -> {1}".format(role, schema[scope].get(role) or "!! UNRESOLVED"))

	parent, child = schema["parent"], schema["child"]

	# Gender source
	print("\n--- Gender source ---")
	if not parent.get("patient"):
		print("  DISABLED - no field for role 'patient' on {0}, cannot join Patient".format(PARENT_DOCTYPE))
	elif not schema["patient"].get("patient_sex"):
		print("  DISABLED - no sex/gender field resolved on {0}".format(PATIENT_DOCTYPE))
	else:
		print(
			"  reading gender from {0}.{1} via {2}.{3}".format(
				PATIENT_DOCTYPE, schema["patient"]["patient_sex"], PARENT_DOCTYPE, parent["patient"]
			)
		)

	print("\n--- Docstatus filter ---")
	print("  reading only {0}.docstatus == {1}".format(PARENT_DOCTYPE, DOCSTATUS))

	# Type restriction
	print("\n--- Type restriction ---")
	if RESULT_TYPE is None:
		print("  disabled (RESULT_TYPE is None) - all types read")
	elif not parent.get("type"):
		print("  BROKEN - no field for role 'type'. The report will refuse to run.")
		print("  Add your real fieldname to FIELD_MAP['type'].")
	else:
		print("  reading only {0}.{1} == '{2}'".format(PARENT_DOCTYPE, parent["type"], RESULT_TYPE))
		rows = frappe.db.sql(
			"SELECT `{0}` AS t, COUNT(*) AS n FROM `tab{1}` GROUP BY `{0}` LIMIT 20".format(
				parent["type"], PARENT_DOCTYPE
			),
			as_dict=True,
		)
		for row in rows:
			mark = "   <== selected" if row.t == RESULT_TYPE else ""
			print("    {0:<20} {1}{2}".format(row.t or "(blank)", row.n, mark))
		if not any(r.t == RESULT_TYPE for r in rows):
			print("    WARNING: no rows have type '{0}' - report will be empty".format(RESULT_TYPE))

	# Required child fields
	print("\n--- Child table ---")
	for role in ("test_name", "result_value"):
		if not child.get(role):
			print("  BROKEN - no field for required role '{0}'".format(role))
	if child.get("test_name") and child.get("result_value"):
		print("  ok: test name = {0}, result = {1}".format(child["test_name"], child["result_value"]))

	if child.get("min_value") or child.get("max_value"):
		print("  abnormal detection: numeric bounds (exact)")
	elif child.get("normal_range"):
		print("  abnormal detection: free-text parsing (heuristic)")
	else:
		print("  abnormal detection DISABLED - no reference range field")

	# Age
	if not parent.get("patient_age"):
		print("\nAge charts DISABLED - unresolved: patient_age")
	elif not parent.get("patient_age_type"):
		print("\nAge charts will show bare numbers - unresolved: patient_age_type (Day/Month/Year)")

	# Statuses
	if parent.get("status"):
		print("\nStatus values in your data:")
		rows = frappe.db.sql(
			"SELECT `{0}` AS s, COUNT(*) AS n FROM `tab{1}` "
			"WHERE `{0}` IS NOT NULL AND `{0}` != '' GROUP BY `{0}` LIMIT 20".format(
				parent["status"], PARENT_DOCTYPE
			),
			as_dict=True,
		)
		for row in rows:
			mark = "   (counted as complete)" if row.s in COMPLETED_STATUSES else ""
			print("  {0:<20} {1}{2}".format(row.s, row.n, mark))
		if not rows:
			print("  (no data yet)")

	return schema


def audit_ranges(min_rows=20, outlier_threshold=0.9):
	"""Find reference ranges that are broken rather than clinically interesting.

	Two problems this catches, both of which quietly poison the abnormal rate:

	  1. UNIT MISMATCH. If a range is written in one unit and results are
	     entered in another (MCHC as 320-360 g/L against results of ~33 g/dL),
	     every single result flags abnormal. A test where nearly ALL results
	     fall outside the range is almost never real pathology — it is a units
	     or data-entry problem.

	  2. UNPARSEABLE RANGES. Ranges that produce no verdict at all, ranked by
	     how many rows they affect, so you fix the ones that matter first.

	Run:
	    bench --site your-site execute \
	        your_app.healthcare.report.lab_result_analytics.lab_result_analytics.audit_ranges
	"""
	schema = get_schema()
	child = schema["child"]

	if not child.get("test_name") or not child.get("result_value"):
		print("Cannot audit: test_name or result_value unresolved. Run inspect() first.")
		return

	fields = ["parent", child["test_name"], child["result_value"]]
	if child.get("normal_range"):
		fields.append(child["normal_range"])
	for role in ("min_value", "max_value"):
		if child.get(role):
			fields.append(child[role])

	rows = frappe.get_all(
		CHILD_DOCTYPE, filters={"parenttype": PARENT_DOCTYPE}, fields=fields, limit_page_length=0
	)

	has_bounds = bool(child.get("min_value") or child.get("max_value"))
	groups = {}

	for row in rows:
		key = (row.get(child["test_name"]) or "(no test name)", row.get(child.get("normal_range")) or None)
		group = groups.setdefault(key, {"n": 0, "outside": 0, "verdicts": 0})
		group["n"] += 1

		value = to_float(row.get(child["result_value"]))
		verdict = None
		if has_bounds:
			verdict = classify_by_bounds(
				value, to_float(row.get(child.get("min_value"))), to_float(row.get(child.get("max_value")))
			)
		if verdict is None and child.get("normal_range"):
			verdict = classify_by_text(value, row.get(child["normal_range"]))
		if verdict is None and child.get("normal_range"):
			verdict = classify_qualitative(row.get(child["result_value"]), row.get(child["normal_range"]))

		if verdict:
			group["verdicts"] += 1
			if verdict != "Normal":
				group["outside"] += 1

	print("\n=== SUSPECT RANGES (>{0:.0%} of results outside, n>={1}) ===".format(outlier_threshold, min_rows))
	print("Almost certainly a unit mismatch or a typo, not real pathology.\n")
	suspects = [
		(k, v)
		for k, v in groups.items()
		if v["verdicts"] >= min_rows and v["outside"] / v["verdicts"] >= outlier_threshold
	]
	if suspects:
		for (test, rng), stat in sorted(suspects, key=lambda x: -x[1]["verdicts"]):
			print(
				"  {0:<28} range {1:<18} {2}/{3} outside ({4:.0%})".format(
					test[:28], str(rng)[:18], stat["outside"], stat["verdicts"], stat["outside"] / stat["verdicts"]
				)
			)
	else:
		print("  none found")

	print("\n=== UNPARSEABLE RANGES (no verdict produced) ===")
	print("Ranked by rows affected. Fix from the top.\n")
	unparseable = [(k, v) for k, v in groups.items() if v["verdicts"] == 0 and v["n"] >= 1]
	for (test, rng), stat in sorted(unparseable, key=lambda x: -x[1]["n"])[:25]:
		print("  {0:<28} range {1:<20} {2} rows".format(test[:28], str(rng)[:20], stat["n"]))
	if not unparseable:
		print("  none")

	evaluated = sum(v["verdicts"] for v in groups.values())
	total = sum(v["n"] for v in groups.values())
	print(
		"\nCoverage: {0}/{1} analyte rows evaluated ({2:.0%}). "
		"The rest show as Not Evaluated.".format(evaluated, total, evaluated / total if total else 0)
	)

	return groups


# ==========================================================================
# Report entry point
# ==========================================================================


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	schema = get_schema()
	parent, child = schema["parent"], schema["child"]

	if not parent:
		frappe.throw(_("DocType {0} not found on this site.").format(PARENT_DOCTYPE))

	# Refuse rather than quietly widen the dataset.
	if RESULT_TYPE is not None and not parent.get("type"):
		frappe.throw(
			_(
				"This report reads only {0} results, but no type field was found on {1}. "
				"Add your fieldname to FIELD_MAP['type'], or set RESULT_TYPE = None to read all types."
			).format(RESULT_TYPE, PARENT_DOCTYPE)
		)

	for role in ("test_name", "result_value"):
		if not child.get(role):
			frappe.throw(
				_("No field found on {0} for '{1}'. Add it to CHILD_FIELD_MAP and reload.").format(
					CHILD_DOCTYPE, role
				)
			)

	data = get_data(filters, schema)

	columns = get_columns(schema)
	chart = get_chart(data, filters)
	report_summary = get_report_summary(data, filters, schema)

	return columns, data, None, chart, report_summary


def validate_filters(filters):
	if filters.from_date and filters.to_date and getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


# ==========================================================================
# Columns — one row per analyte
# ==========================================================================

PARENT_COLUMN_SPECS = [
	("ordered_at", {"label": _("Ordered"), "fieldtype": "Datetime", "width": 150}),
	("patient", {"label": _("Patient"), "fieldtype": "Link", "options": "Patient", "width": 110}),
	("patient_name", {"label": _("Patient Name"), "fieldtype": "Data", "width": 350}),
	# Gender is handled separately in get_columns() - it's sourced from the
	# Patient master via a join, not resolved as a role on `parent`.
	("patient_age", {"label": _("Age"), "fieldtype": "Data", "width": 70}),
	("patient_age_type", {"label": _("Age Type"), "fieldtype": "Data", "width": 80}),
]

TRAILING_PARENT_SPECS = [
	("section", {"label": _("Section"), "fieldtype": "Data", "width": 120}),
	(
		"department",
		{"label": _("Department"), "fieldtype": "Link", "options": "Medical Department", "width": 130},
	),
	(
		"practitioner",
		{"label": _("Practitioner"), "fieldtype": "Link", "options": "Healthcare Practitioner", "width": 140},
	),
	("technician", {"label": _("Technician"), "fieldtype": "Data", "width": 130}),
	("status", {"label": _("Status"), "fieldtype": "Data", "width": 100}),
]


def get_columns(schema):
	parent, child = schema["parent"], schema["child"]
	patient_schema = schema.get("patient") or {}

	columns = [
		{
			"label": _("Lab Result"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": PARENT_DOCTYPE,
			"width": 200,
		}
	]

	for role, spec in PARENT_COLUMN_SPECS:
		if parent.get(role):
			column = dict(spec)
			column["fieldname"] = role
			columns.append(column)

	# Gender is sourced from the Patient master via a join, not resolved as a
	# role on `parent`, so it's added here rather than through the loop above.
	if parent.get("patient") and patient_schema.get("patient_sex"):
		columns.append({"label": _("Gender"), "fieldname": "patient_sex", "fieldtype": "Data", "width": 80})

	# The analyte block — the point of the report.
	columns.append({"label": _("Test"), "fieldname": "test_name", "fieldtype": "Data", "width": 180})
	columns.append({"label": _("Result"), "fieldname": "result_value", "fieldtype": "Data", "width": 100})
	if child.get("uom"):
		columns.append({"label": _("Unit"), "fieldname": "uom", "fieldtype": "Data", "width": 80})
	columns.append(
		{"label": _("Reference Range"), "fieldname": "reference_range", "fieldtype": "Data", "width": 130}
	)
	columns.append({"label": _("Flag"), "fieldname": "result_flag", "fieldtype": "Data", "width": 110})

	for role, spec in TRAILING_PARENT_SPECS:
		if parent.get(role):
			column = dict(spec)
			column["fieldname"] = role
			columns.append(column)

	return columns


# ==========================================================================
# Data — parent JOIN child, one row per analyte
# ==========================================================================


def get_conditions(filters, parent, patient_schema=None):
	patient_schema = patient_schema or {}
	conditions = ""

	# The hard type restriction, applied first and always.
	if RESULT_TYPE is not None and parent.get("type"):
		conditions += " AND lr.`{0}` = %(result_type)s".format(parent["type"])

	if parent.get("ordered_at"):
		if filters.get("from_date"):
			conditions += " AND DATE(lr.`{0}`) >= %(from_date)s".format(parent["ordered_at"])
		if filters.get("to_date"):
			conditions += " AND DATE(lr.`{0}`) <= %(to_date)s".format(parent["ordered_at"])

	for role, param in (
		("company", "company"),
		("department", "department"),
		("practitioner", "practitioner"),
		("status", "status"),
		("patient_age_type", "patient_age_type"),
	):
		if filters.get(param) and parent.get(role):
			conditions += " AND lr.`{0}` = %({1})s".format(parent[role], param)

	# Gender lives on the joined Patient master, not on Lab Result.
	if filters.get("patient_sex") and parent.get("patient") and patient_schema.get("patient_sex"):
		conditions += " AND pt.`{0}` = %(patient_sex)s".format(patient_schema["patient_sex"])

	return conditions


def get_data(filters, schema):
	parent, child = schema["parent"], schema["child"]
	patient_schema = schema.get("patient") or {}

	# Alias every resolved field to its role name so columns, charts and the
	# .js all key on stable names regardless of your schema.
	select_list = ["lr.name", "lr.docstatus"]
	for role, fieldname in parent.items():
		select_list.append("lr.`{0}` AS `{1}`".format(fieldname, role))
	for role, fieldname in child.items():
		select_list.append("ntr.`{0}` AS `{1}`".format(fieldname, role))

	# Gender always comes from the Patient master, joined via the "patient"
	# role, never from a field on Lab Result itself.
	patient_join = ""
	can_join_patient = bool(parent.get("patient") and patient_schema.get("patient_sex"))
	if can_join_patient:
		patient_join = "LEFT JOIN `tab{0}` pt ON pt.name = lr.`{1}`".format(PATIENT_DOCTYPE, parent["patient"])
		select_list.append("pt.`{0}` AS `patient_sex`".format(patient_schema["patient_sex"]))
	else:
		select_list.append("NULL AS `patient_sex`")

	order_by = "lr.`{0}`".format(parent["ordered_at"]) if parent.get("ordered_at") else "lr.creation"

	child_condition = ""
	if filters.get("test_name"):
		child_condition += " AND ntr.`{0}` LIKE %(test_name_like)s".format(child["test_name"])

	query_params = dict(filters)
	query_params["result_type"] = RESULT_TYPE
	query_params["docstatus"] = DOCSTATUS
	if filters.get("test_name"):
		query_params["test_name_like"] = "%{0}%".format(filters["test_name"])

	rows = frappe.db.sql(
		"""
		SELECT {select_list}
		FROM `tab{parent_doctype}` lr
		INNER JOIN `tab{child_doctype}` ntr
			ON ntr.parent = lr.name AND ntr.parenttype = %(child_parenttype)s
		{patient_join}
		WHERE lr.docstatus = %(docstatus)s {conditions} {child_condition}
		ORDER BY {order_by} DESC, ntr.idx ASC
		""".format(
			select_list=", ".join(select_list),
			parent_doctype=PARENT_DOCTYPE,
			child_doctype=CHILD_DOCTYPE,
			patient_join=patient_join,
			conditions=get_conditions(filters, parent, patient_schema),
			child_condition=child_condition,
			order_by=order_by,
		),
		dict(query_params, child_parenttype=PARENT_DOCTYPE),
		as_dict=True,
	)

	for row in rows:
		for role in FIELD_MAP:
			row.setdefault(role, None)
		for role in CHILD_FIELD_MAP:
			row.setdefault(role, None)
		row.setdefault("patient_sex", None)

	attach_result_flags(rows, schema)
	attach_age_group(rows, schema)

	return rows


# ==========================================================================
# Age grouping — combines patient_age with patient_age_type into one label
# ("25 Year", "6 Month", "10 Day") so age charts read naturally instead of
# mixing units. Falls back to the bare number if age_type never resolved.
# ==========================================================================


def attach_age_group(data, schema):
	parent = schema["parent"]
	has_type = bool(parent.get("patient_age_type"))

	for row in data:
		age = row.get("patient_age")
		if age in (None, ""):
			row["age_group"] = None
			continue
		age_type = row.get("patient_age_type") if has_type else None
		row["age_group"] = "{0} {1}".format(age, age_type).strip() if age_type else str(age)


# ==========================================================================
# Abnormal result detection — per analyte
# ==========================================================================

# Anchored at the start but NOT at the end, so a trailing unit is ignored:
#   "0.8-1.4mg/dl" -> 0.8 to 1.4
#   "0-2 Casts/HPF" -> 0 to 2
#   "27.0 -34.0"   -> 27.0 to 34.0
RANGE_BETWEEN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
RANGE_UPPER = re.compile(r"^\s*[<≤]\s*=?\s*(-?\d+(?:\.\d+)?)")
RANGE_LOWER = re.compile(r"^\s*[>≥]\s*=?\s*(-?\d+(?:\.\d+)?)")

# A result entered as a span, e.g. "0-1" for cells per HPF. The upper bound is
# the conservative reading: if the top of the observed span is inside the
# reference range, the result is normal.
VALUE_SPAN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
VALUE_LEADING = re.compile(r"^\s*[<>≤≥]?\s*=?\s*(-?\d+(?:\.\d+)?)")

# Text meaning "nothing found". A reference range of "Nill" and a result of
# "Nil" agree; a result of "Few uric acid crystals" does not.
NEGATIVE_TERMS = {
	"nil",
	"nill",
	"none",
	"negative",
	"neg",
	"absent",
	"not detected",
	"nd",
	"normal",
	"no growth",
}

# Set False to report every non-numeric result as Not Evaluated instead of
# comparing text. Text comparison is weaker evidence than a numeric one.
ENABLE_QUALITATIVE = True


def normalise_text(value):
	"""Lowercase, drop punctuation, collapse whitespace.

	Makes 'pale- yellow' and 'pale yellow ' compare equal.
	"""
	return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def to_float(value):
	"""Parse a result value leniently.

	Handles a plain number, a number with a trailing unit, an operator prefix
	('<0.5'), and a span ('0-1', read as its upper bound).
	"""
	if value is None:
		return None

	text = str(value).strip()
	if not text:
		return None

	try:
		return float(text)
	except ValueError:
		pass

	match = VALUE_SPAN.match(text)
	if match:
		return float(match.group(2))

	match = VALUE_LEADING.match(text)
	if match:
		return float(match.group(1))

	return None


def classify_by_bounds(value, low, high):
	"""Exact comparison against numeric bounds. Preferred when available."""
	if value is None:
		return None
	if low is not None and value < low:
		return "Low"
	if high is not None and value > high:
		return "High"
	if low is None and high is None:
		return None
	return "Normal"


def classify_by_text(value, normal_range):
	"""Best-effort parse of 'a - b', '< b', '> a'.

	Anything else — qualitative results like "Negative", sex-specific ranges,
	multi-line text — returns None and shows as Not Evaluated rather than being
	guessed at. A lab report that quietly mislabels a result is worse than one
	that admits doubt.
	"""
	if value is None:
		return None

	text = (normal_range or "").strip()
	if not text:
		return None

	match = RANGE_BETWEEN.match(text)
	if match:
		low, high = float(match.group(1)), float(match.group(2))
		if value < low:
			return "Low"
		return "Normal" if value <= high else "High"

	match = RANGE_UPPER.match(text)
	if match:
		return "Normal" if value <= float(match.group(1)) else "High"

	match = RANGE_LOWER.match(text)
	if match:
		return "Normal" if value >= float(match.group(1)) else "Low"

	return None


def classify_qualitative(result_value, normal_range):
	"""Compare non-numeric results against a non-numeric reference.

	Deliberately conservative — only three situations produce a verdict:

	  * the normalised strings match exactly       -> Normal
	  * both sides mean "nothing found"            -> Normal
	  * the reference means "nothing found" and the
	    result is clearly something else           -> Abnormal

	Anything else returns None. A semi-quantitative result like "+" against a
	reference of "Few/HPF" is genuinely ambiguous, and inventing a verdict for
	it would put a wrong flag on a patient's record.
	"""
	if not ENABLE_QUALITATIVE:
		return None

	result = normalise_text(result_value)
	reference = normalise_text(normal_range)

	if not result or not reference:
		return None

	if result == reference:
		return "Normal"

	result_negative = result in NEGATIVE_TERMS
	reference_negative = reference in NEGATIVE_TERMS

	if reference_negative and result_negative:
		return "Normal"
	if reference_negative and not result_negative:
		return "Abnormal"

	return None


def format_range(row, has_bounds):
	if has_bounds:
		low, high = to_float(row.get("min_value")), to_float(row.get("max_value"))
		if low is not None and high is not None:
			return "{0} - {1}".format(low, high)
		if high is not None:
			return "< {0}".format(high)
		if low is not None:
			return "> {0}".format(low)
	return row.get("normal_range") or ""


def attach_result_flags(data, schema):
	child = schema["child"]
	has_bounds = bool(child.get("min_value") or child.get("max_value"))
	labels = {
		"Normal": _("Normal"),
		"High": _("High"),
		"Low": _("Low"),
		"Abnormal": _("Abnormal"),
	}

	for row in data:
		row["reference_range"] = format_range(row, has_bounds)
		row["result_flag"] = _("Not Evaluated")

		value = to_float(row.get("result_value"))

		# 1. Numeric bounds — exact, preferred.
		verdict = None
		if has_bounds:
			verdict = classify_by_bounds(value, to_float(row.get("min_value")), to_float(row.get("max_value")))

		# 2. Numeric range parsed from text.
		if verdict is None and child.get("normal_range"):
			verdict = classify_by_text(value, row.get("normal_range"))

		# 3. Qualitative text comparison, only where it is unambiguous.
		if verdict is None and child.get("normal_range"):
			verdict = classify_qualitative(row.get("result_value"), row.get("normal_range"))

		if verdict:
			row["result_flag"] = labels[verdict]


# ==========================================================================
# Built-in chart and KPI strip
#
# The built-in chart is a Normal vs Abnormal comparison, bucketed by the
# period picked in the "Group Chart By" filter (Daily / Weekly / Monthly /
# Quarterly / Yearly), counted over analyte rows.
# ==========================================================================


def get_period_key(value, period):
	d = getdate(value)

	if period == "Daily":
		return d.strftime("%Y-%m-%d")
	if period == "Weekly":
		year, week, _weekday = d.isocalendar()
		return "{0}-W{1:02d}".format(year, week)
	if period == "Quarterly":
		return "{0}-Q{1}".format(d.year, (d.month - 1) // 3 + 1)
	if period == "Yearly":
		return str(d.year)

	return d.strftime("%Y-%m")


def get_chart(data, filters):
	period = filters.get("range") or "Monthly"
	buckets = {}

	for row in data:
		if not row.get("ordered_at"):
			continue
		key = get_period_key(row["ordered_at"], period)
		bucket = buckets.setdefault(key, {"normal": 0, "abnormal": 0, "not_evaluated": 0})

		flag = row.get("result_flag")
		if flag == _("Normal"):
			bucket["normal"] += 1
		elif flag in (_("High"), _("Low"), _("Abnormal")):
			bucket["abnormal"] += 1
		else:
			bucket["not_evaluated"] += 1

	labels = sorted(buckets.keys())

	datasets = [
		{"name": _("Normal"), "values": [buckets[k]["normal"] for k in labels], "chartType": "bar"},
		{"name": _("Abnormal"), "values": [buckets[k]["abnormal"] for k in labels], "chartType": "bar"},
	]

	return {
		"data": {"labels": labels, "datasets": datasets},
		# frappe-charts types: bar, line, pie, donut, percentage, heatmap, axis-mixed
		"type": "bar",
		"colors": ["#28a745", "#e24b4a"],
		"axisOptions": {"xIsSeries": 1, "shortenYAxisNumbers": 1},
		"barOptions": {"spaceRatio": 0.4},
	}


def get_report_summary(data, filters, schema):
	if not data:
		return []

	parent = schema["parent"]

	analytes = len(data)
	documents = len({row["name"] for row in data})
	patients = len({row["patient"] for row in data if row.get("patient")})

	summary = [
		{"label": _("Tests Performed"), "value": analytes, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Lab Results"), "value": documents, "datatype": "Int", "indicator": "Blue"},
	]

	if patients:
		summary.append({"label": _("Patients"), "value": patients, "datatype": "Int", "indicator": "Blue"})

	evaluated = [d for d in data if d.get("result_flag") != _("Not Evaluated")]
	if evaluated:
		normal = sum(1 for d in evaluated if d.get("result_flag") == _("Normal"))
		abnormal = len(evaluated) - normal
		summary.append({"label": _("Normal Results"), "value": normal, "datatype": "Int", "indicator": "Green"})
		summary.append(
			{
				"label": _("Abnormal Rate"),
				"value": "{0}%".format(round(abnormal / len(evaluated) * 100, 1)),
				"datatype": "Data",
				"indicator": "Red" if abnormal / len(evaluated) > 0.4 else "Orange",
			}
		)

	if parent.get("status"):
		completed = len({d["name"] for d in data if d.get("status") in COMPLETED_STATUSES})
		summary.append({"label": _("Completed"), "value": completed, "datatype": "Int", "indicator": "Green"})

	return summary


# ==========================================================================
# Dashboard builder
#
# Run: bench --site your-site execute
#      your_app.healthcare.report.lab_result_analytics.lab_result_analytics.setup_dashboard
#
# Note: Dashboard Charts group by a field on ONE doctype, so charts built from
# `Lab Result` count DOCUMENTS, not analytes. Analyte-level charts (top tests,
# normal-vs-abnormal by practitioner/department/gender/age) come from the
# report's own .js, or from the Custom chart sources below.
# ==========================================================================


def base_filters(parent):
	"""Filters applied to every dashboard chart/card: docstatus, then type."""
	filters = [[PARENT_DOCTYPE, "docstatus", "=", DOCSTATUS]]
	if RESULT_TYPE is not None and parent.get("type"):
		filters.append([PARENT_DOCTYPE, parent["type"], "=", RESULT_TYPE])
	return filters


def type_filter_json(parent):
	"""Kept for backwards compatibility - now includes the docstatus filter too."""
	return json.dumps(base_filters(parent))


def build_dashboard_charts(parent):
	charts = []
	filters_json = type_filter_json(parent)

	def group_by(name, role, chart_type, width, groups=8, colour=None):
		if not parent.get(role):
			print("  skip chart '{0}' - no field for role '{1}'".format(name, role))
			return
		spec = {
			"chart_name": name,
			"chart_type": "Group By",
			"document_type": PARENT_DOCTYPE,
			"group_by_type": "Count",
			"group_by_based_on": parent[role],
			"number_of_groups": groups,
			"type": chart_type,
			"width": width,
		}
		if filters_json:
			spec["filters_json"] = filters_json
		if colour:
			spec["color"] = colour
		charts.append(spec)

	if parent.get("ordered_at"):
		spec = {
			"chart_name": "Blood Result Volume Trend",
			"chart_type": "Count",
			"document_type": PARENT_DOCTYPE,
			"based_on": parent["ordered_at"],
			"time_interval": "Monthly",
			"timespan": "Last Year",
			"type": "Line",
			"width": "Full",
			"color": "#e24b4a",
		}
		if filters_json:
			spec["filters_json"] = filters_json
		charts.append(spec)
	else:
		print("  skip volume trend - no field for role 'ordered_at'")

	# Normal vs Abnormal breakdown charts — analyte-level, so they need the
	# Custom chart sources below rather than a simple document Group By.
	for source_name, chart_title in (
		(NORMAL_ABNORMAL_BY_PRACTITIONER_SOURCE, "Blood Results — Normal vs Abnormal by Doctor"),
		(NORMAL_ABNORMAL_BY_DEPARTMENT_SOURCE, "Blood Results — Normal vs Abnormal by Department"),
		(NORMAL_ABNORMAL_BY_GENDER_SOURCE, "Blood Results — Normal vs Abnormal by Gender"),
		(NORMAL_ABNORMAL_BY_AGE_SOURCE, "Blood Results — Normal vs Abnormal by Age Group"),
	):
		if frappe.db.exists("Dashboard Chart Source", source_name):
			charts.append(
				{
					"chart_name": chart_title,
					"chart_type": "Custom",
					"source": source_name,
					"type": "Bar",
					"width": "Full",
				}
			)

	group_by("Blood Result Status Mix", "status", "Donut", "Half")
	group_by("Blood Volume by Department", "department", "Pie", "Half")
	# NOTE: no plain document-count "Gender" Group By chart here - gender is
	# no longer a field on Lab Result, it lives on the joined Patient master,
	# and a "Group By" Dashboard Chart can only group by a field on
	# document_type itself. The "Normal vs Abnormal by Gender" custom chart
	# above covers the gender breakdown instead.
	group_by("Blood Volume by Section", "section", "Percentage", "Half", groups=6)
	group_by("Blood Orders by Doctor", "practitioner", "Bar", "Half", groups=10, colour="#743ee2")
	group_by("Blood Workload by Technician", "technician", "Bar", "Half", groups=10, colour="#5e64ff")

	if frappe.db.exists("Report", REPORT_NAME):
		charts.append(
			{
				"chart_name": "Blood Analytics (from Report)",
				"chart_type": "Report",
				"report_name": REPORT_NAME,
				"type": "Bar",
				"width": "Full",
			}
		)

	return charts


def build_dashboard_cards(parent):
	base = base_filters(parent)

	cards = [
		{
			"label": "Blood Results This Month",
			"document_type": PARENT_DOCTYPE,
			"function": "Count",
			"filters_json": json.dumps(base),
			"stats_time_interval": "Monthly",
		}
	]

	if parent.get("status"):
		where = " AND `docstatus` = {0}".format(DOCSTATUS)
		if parent.get("type"):
			where += " AND `{0}` = '{1}'".format(parent["type"], RESULT_TYPE)
		values = [
			row[0]
			for row in frappe.db.sql(
				"SELECT DISTINCT `{0}` FROM `tab{1}` "
				"WHERE `{0}` IS NOT NULL AND `{0}` != ''{2} LIMIT 10".format(
					parent["status"], PARENT_DOCTYPE, where
				)
			)
		]

		labels = {
			"Draft": "Blood — Awaiting Results",
			"Pending": "Blood — Awaiting Results",
			"In Process": "Blood — In Process",
			"Completed": "Blood — Completed",
			"Approved": "Blood — Approved",
			"Verified": "Blood — Verified",
			"Rejected": "Blood — Rejected",
			"Cancelled": "Blood — Cancelled",
		}

		if not values:
			print("  note: no status values found - status cards skipped")

		for value in values:
			if value in labels:
				cards.append(
					{
						"label": labels[value],
						"document_type": PARENT_DOCTYPE,
						"function": "Count",
						"filters_json": json.dumps(base + [[PARENT_DOCTYPE, parent["status"], "=", value]]),
						"stats_time_interval": "Monthly",
					}
				)
			else:
				print("  note: unmapped status '{0}' - add it to build_dashboard_cards()".format(value))

	if parent.get("invoiced"):
		cards.append(
			{
				"label": "Blood — Unbilled",
				"document_type": PARENT_DOCTYPE,
				"function": "Count",
				"filters_json": json.dumps(base + [[PARENT_DOCTYPE, parent["invoiced"], "=", 0]]),
				"stats_time_interval": "Monthly",
			}
		)

	return cards


def setup_dashboard():
	if not frappe.db.exists("DocType", PARENT_DOCTYPE):
		frappe.throw("DocType '{0}' not found on this site.".format(PARENT_DOCTYPE))

	parent = get_schema()["parent"]

	print("Resolved fields on {0}:".format(PARENT_DOCTYPE))
	for role in FIELD_MAP:
		print("  {0:<18} -> {1}".format(role, parent.get(role) or "!! UNRESOLVED"))

	if RESULT_TYPE is not None and not parent.get("type"):
		frappe.throw(
			"No field for role 'type'. Charts would include every result type, "
			"not just {0}. Fix FIELD_MAP['type'] first.".format(RESULT_TYPE)
		)
	print("\nType restriction: {0}\n".format(RESULT_TYPE or "none"))

	create_chart_sources()

	charts = []
	for spec in build_dashboard_charts(parent):
		name = upsert(
			"Dashboard Chart",
			spec["chart_name"],
			spec,
			extra={"is_public": 1, "timeseries": 1 if spec.get("based_on") else 0},
		)
		if name:
			charts.append((name, spec.get("width", "Half")))

	cards = []
	for spec in build_dashboard_cards(parent):
		name = upsert(
			"Number Card",
			spec["label"],
			spec,
			extra={"type": "Document Type", "is_public": 1, "show_percentage_stats": 1},
		)
		if name:
			cards.append(name)

	create_dashboard(charts, cards)
	frappe.db.commit()

	print("\nLaboratory dashboard: {0} charts, {1} cards.".format(len(charts), len(cards)))
	print("Open: /app/dashboard-view/{0}".format(DASHBOARD_NAME.replace(" ", "%20")))


def upsert(doctype, name, spec, extra=None):
	spec = spec.copy()
	spec.pop("width", None)

	doc = frappe.get_doc(doctype, name) if frappe.db.exists(doctype, name) else frappe.new_doc(doctype)
	doc.update(spec)
	if extra:
		doc.update(extra)
	doc.name = name
	doc.save(ignore_permissions=True)
	return doc.name


def create_dashboard(charts, cards):
	doc = (
		frappe.get_doc("Dashboard", DASHBOARD_NAME)
		if frappe.db.exists("Dashboard", DASHBOARD_NAME)
		else frappe.new_doc("Dashboard")
	)
	doc.dashboard_name = DASHBOARD_NAME
	doc.set("charts", [])
	doc.set("cards", [])

	for card in cards:
		doc.append("cards", {"card": card})
	for chart, width in charts:
		doc.append("charts", {"chart": chart, "width": width})

	doc.save(ignore_permissions=True)
	return doc.name


def teardown_dashboard():
	"""Remove everything setup_dashboard() created."""
	if frappe.db.exists("Dashboard", DASHBOARD_NAME):
		frappe.delete_doc("Dashboard", DASHBOARD_NAME, force=True)

	parent = get_schema()["parent"]
	for spec in build_dashboard_charts(parent):
		if frappe.db.exists("Dashboard Chart", spec["chart_name"]):
			frappe.delete_doc("Dashboard Chart", spec["chart_name"], force=True)
	for spec in build_dashboard_cards(parent):
		if frappe.db.exists("Number Card", spec["label"]):
			frappe.delete_doc("Number Card", spec["label"], force=True)

	for source_name in (
		NORMAL_ABNORMAL_BY_PRACTITIONER_SOURCE,
		NORMAL_ABNORMAL_BY_DEPARTMENT_SOURCE,
		NORMAL_ABNORMAL_BY_GENDER_SOURCE,
		NORMAL_ABNORMAL_BY_AGE_SOURCE,
	):
		if frappe.db.exists("Dashboard Chart Source", source_name):
			frappe.delete_doc("Dashboard Chart Source", source_name, force=True)

	frappe.db.commit()
	print("Removed the {0} dashboard.".format(DASHBOARD_NAME))


# ==========================================================================
# Custom chart sources: Normal vs Abnormal, grouped by document dimension
#
# Normal/Abnormal is an ANALYTE-level fact, so it can't be produced by a
# built-in "Group By" Dashboard Chart (those group and count DOCUMENTS).
# Each of these fetches the same parent+child join the report uses, then
# buckets analyte rows by one parent-level dimension.
#
# Each Dashboard Chart Source also needs a companion JS file or the chart
# renders empty with no error. Create:
#
#   your_app/healthcare/dashboard_chart_source/
#       lab_normal_abnormal_by_practitioner/lab_normal_abnormal_by_practitioner.js
#       lab_normal_abnormal_by_department/lab_normal_abnormal_by_department.js
#       lab_normal_abnormal_by_gender/lab_normal_abnormal_by_gender.js
#       lab_normal_abnormal_by_age_group/lab_normal_abnormal_by_age_group.js
#
#   frappe.provide("frappe.dashboards.chart_sources");
#   frappe.dashboards.chart_sources["<Source Name>"] = {
#       method: "your_app.healthcare.report.lab_result_analytics"
#               ".lab_result_analytics.<matching get_ function>",
#       filters: [],
#   };
# ==========================================================================

NORMAL_ABNORMAL_BY_PRACTITIONER_SOURCE = "Lab Normal vs Abnormal by Doctor"
NORMAL_ABNORMAL_BY_DEPARTMENT_SOURCE = "Lab Normal vs Abnormal by Department"
NORMAL_ABNORMAL_BY_GENDER_SOURCE = "Lab Normal vs Abnormal by Gender"
NORMAL_ABNORMAL_BY_AGE_SOURCE = "Lab Normal vs Abnormal by Age Group"


def get_normal_abnormal_grouped(group_role, limit=10):
	"""Normal/Abnormal analyte counts grouped by a resolved dimension.

	group_role is one of the FIELD_MAP roles (e.g. "practitioner",
	"department"), the derived "age_group" role, or "patient_sex" - which is
	resolved separately since it comes from the joined Patient master, not
	from a field on Lab Result itself.
	"""
	schema = get_schema()
	parent = schema["parent"]
	patient_schema = schema.get("patient") or {}

	if group_role == "patient_sex":
		available = bool(parent.get("patient") and patient_schema.get("patient_sex"))
	elif group_role == "age_group":
		available = True
	else:
		available = bool(parent.get(group_role))

	if not available:
		return {"labels": [], "datasets": [{"name": _("Normal"), "values": []}, {"name": _("Abnormal"), "values": []}]}

	data = get_data(frappe._dict(), schema)

	buckets = {}
	for row in data:
		flag = row.get("result_flag")
		if flag == _("Not Evaluated"):
			continue

		key = row.get(group_role) or _("Not Set")
		bucket = buckets.setdefault(key, {"normal": 0, "abnormal": 0})
		if flag == _("Normal"):
			bucket["normal"] += 1
		else:
			bucket["abnormal"] += 1

	top_keys = sorted(buckets.keys(), key=lambda k: -(buckets[k]["normal"] + buckets[k]["abnormal"]))[:limit]

	return {
		"labels": top_keys,
		"datasets": [
			{"name": _("Normal"), "values": [buckets[k]["normal"] for k in top_keys]},
			{"name": _("Abnormal"), "values": [buckets[k]["abnormal"] for k in top_keys]},
		],
	}


@frappe.whitelist()
def get_normal_abnormal_by_practitioner(**kwargs):
	"""Normal vs Abnormal, by ordering doctor. Source: Lab Normal vs Abnormal by Doctor."""
	return get_normal_abnormal_grouped("practitioner")


@frappe.whitelist()
def get_normal_abnormal_by_department(**kwargs):
	"""Normal vs Abnormal, by department. Source: Lab Normal vs Abnormal by Department."""
	return get_normal_abnormal_grouped("department")


@frappe.whitelist()
def get_normal_abnormal_by_gender(**kwargs):
	"""Normal vs Abnormal, by patient gender. Source: Lab Normal vs Abnormal by Gender."""
	return get_normal_abnormal_grouped("patient_sex")


@frappe.whitelist()
def get_normal_abnormal_by_age_group(**kwargs):
	"""Normal vs Abnormal, by patient age + age type (e.g. "25 Year", "6 Month").

	Source: Lab Normal vs Abnormal by Age Group.
	"""
	return get_normal_abnormal_grouped("age_group")


def create_chart_sources():
	if not frappe.db.exists("DocType", "Dashboard Chart Source"):
		print("  skip custom chart sources - Dashboard Chart Source doctype not found")
		return

	for source_name in (
		NORMAL_ABNORMAL_BY_PRACTITIONER_SOURCE,
		NORMAL_ABNORMAL_BY_DEPARTMENT_SOURCE,
		NORMAL_ABNORMAL_BY_GENDER_SOURCE,
		NORMAL_ABNORMAL_BY_AGE_SOURCE,
	):
		doc = (
			frappe.get_doc("Dashboard Chart Source", source_name)
			if frappe.db.exists("Dashboard Chart Source", source_name)
			else frappe.new_doc("Dashboard Chart Source")
		)
		doc.source_name = source_name
		doc.module = "Healthcare"
		doc.timeseries = 0
		doc.save(ignore_permissions=True)