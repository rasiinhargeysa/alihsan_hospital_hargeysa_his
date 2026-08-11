# Copyright (c) 2026
# For license information, please see license.txt

# ------------------------------------------------------------------------------------
# SINGLE SOURCE OF TRUTH
#
# Every view in this report (summary bar, data table, Detailed dashboard, pie
# charts, Break-Even, Target Profit) now derives Total Income / Total Expenses
# from the SAME place: the parent `Forecast Form` fields `total_income` and
# `total_expenses`, aggregated with get_parent_totals().
#
# Child tables (Forecast Form Income / Forecast Form Expenses) are used ONLY to
# split those totals into categories for the breakdown panels and pie charts.
# If the child rows don't add up to the parent total, the difference is shown
# explicitly as an "Unallocated" slice instead of silently shrinking the total.
# That way the donut, the panel header, the cards and the table always tie out.
#
# ASSUMPTIONS (adjust to match your actual doctype/field names if different):
#   Parent doctype:            Forecast Form
#     Fields: department, date, total_income, total_expenses, profit_or_loss
#   Income child table:        fieldname "income"   -> doctype "Forecast Form Income"
#     Fields: service_income, qty, qty_per_month, price, revenue, revenue_percentage
#   Expenses child table:      fieldname "expenses" -> doctype "Forecast Form Expenses"
#     Fields: department_expenses, expense_type, qty, price, expense, expense_percentage
# ------------------------------------------------------------------------------------

import frappe
from frappe import _
from frappe.utils import flt, getdate

CHILD_INCOME_DOCTYPE = "Forecast Form Income"
CHILD_EXPENSE_DOCTYPE = "Forecast Form Expenses"

# Any difference smaller than this is treated as rounding noise and ignored.
RECONCILE_TOLERANCE = 0.01


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(filters)

	chart_type = filters.get("chart_type") or "Income vs Expenses"

	if chart_type == "Detailed":
		# Custom HTML dashboard (cards + donuts + account breakdown), rendered
		# above the data table via the "message" slot. Frappe's native chart
		# object can only draw ONE chart type, so this view is built by hand.
		message = get_detailed_dashboard_html(filters)
		chart = None
	elif chart_type == "Break-Even Analysis":
		message = get_breakeven_dashboard_html(filters)
		chart = None
	elif chart_type == "Target Profit Analysis":
		message = get_target_profit_dashboard_html(filters)
		chart = None
	else:
		message = None
		chart = get_chart(filters)

	return columns, data, message, chart, report_summary


def validate_filters(filters):
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Please select both From Date and To Date."))

	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date."))


def get_conditions(filters):
	conditions = {
		"date": ["between", [filters.from_date, filters.to_date]],
	}
	if filters.get("department"):
		conditions["department"] = filters.department
	return conditions


def get_matching_parents(filters):
	return frappe.get_all("Forecast Form", filters=get_conditions(filters), pluck="name")


# ------------------------------------------------------------------------------------
# TOTALS — the one function every view calls
# ------------------------------------------------------------------------------------

def get_parent_totals(filters):
	"""Aggregate Total Income / Total Expenses straight from the Forecast Form
	documents. This is the control total for the whole report."""
	rows = frappe.get_all(
		"Forecast Form",
		filters=get_conditions(filters),
		fields=[
			"sum(total_income) as total_income",
			"sum(total_expenses) as total_expenses",
			"count(name) as forecast_count",
		],
	)

	row = rows[0] if rows else frappe._dict()
	total_income = flt(row.get("total_income"))
	total_expenses = flt(row.get("total_expenses"))

	return frappe._dict(
		total_income=total_income,
		total_expenses=total_expenses,
		net_profit=total_income - total_expenses,
		forecast_count=int(flt(row.get("forecast_count"))),
	)


def reconcile_rows(rows, control_total):
	"""Make a set of category rows add up to the control total by appending an
	explicit 'Unallocated' row for any difference. Returns a new list."""
	out = [frappe._dict(label=r.get("label"), total=flt(r.get("total"))) for r in rows]
	gap = flt(control_total) - sum(r.total for r in out)

	if abs(gap) > RECONCILE_TOLERANCE:
		out.append(frappe._dict(label=_(UNALLOCATED_LABEL), total=gap))
		out.sort(key=lambda r: r.total, reverse=True)

	return out


def get_income_breakdown(filters, totals=None):
	"""Income categories that always sum to the parent Total Income."""
	totals = totals or get_parent_totals(filters)
	raw = get_income_rows(filters)
	return reconcile_rows(raw, totals.total_income), sum(flt(r.total) for r in raw)


def get_expense_breakdown(filters, totals=None):
	"""Expense categories that always sum to the parent Total Expenses."""
	totals = totals or get_parent_totals(filters)
	raw = get_expense_rows(filters)
	return reconcile_rows(raw, totals.total_expenses), sum(flt(r.total) for r in raw)


# ------------------------------------------------------------------------------------
# TABLE
# ------------------------------------------------------------------------------------

def get_columns():
	return [
		{"label": _("Forecast Form"), "fieldname": "name", "fieldtype": "Link",
			"options": "Forecast Form", "width": 150},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link",
			"options": "Department", "width": 150},
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{"label": _("Total Income"), "fieldname": "total_income", "fieldtype": "Currency", "width": 130},
		{"label": _("Total Expenses"), "fieldname": "total_expenses", "fieldtype": "Currency", "width": 130},
		{"label": _("Profit / Loss"), "fieldname": "profit_or_loss", "fieldtype": "Currency", "width": 130},
		{"label": _("Margin %"), "fieldname": "margin", "fieldtype": "Percent", "width": 100},
	]


def get_data(filters):
	rows = frappe.get_all(
		"Forecast Form",
		filters=get_conditions(filters),
		fields=["name", "department", "date", "total_income", "total_expenses", "profit_or_loss"],
		order_by="date asc",
	)

	for row in rows:
		# Derive Profit / Loss rather than trusting the stored field, so the
		# column can never drift away from Income - Expenses.
		row["profit_or_loss"] = flt(row.total_income) - flt(row.total_expenses)
		row["margin"] = (
			flt(row.profit_or_loss) / flt(row.total_income) * 100 if flt(row.total_income) else 0
		)

	return rows


def get_report_summary(filters):
	totals = get_parent_totals(filters)
	margin = (totals.net_profit / totals.total_income * 100) if totals.total_income else 0

	return [
		{"label": _("Total Income"), "value": totals.total_income, "datatype": "Currency",
			"indicator": "Blue"},
		{"label": _("Total Expenses"), "value": totals.total_expenses, "datatype": "Currency",
			"indicator": "Orange"},
		{"label": _("Net Profit / Loss"), "value": totals.net_profit, "datatype": "Currency",
			"indicator": "Green" if totals.net_profit >= 0 else "Red"},
		{"label": _("Profit Margin"), "value": margin, "datatype": "Percent",
			"indicator": "Green" if margin >= 0 else "Red"},
		{"label": _("No. of Forecasts"), "value": totals.forecast_count, "datatype": "Int",
			"indicator": "Grey"},
	]


# ------------------------------------------------------------------------------------
# CHART BUILDING
# The "Chart View" filter lets the user switch between several professional chart
# types without leaving the report: a grouped bar chart (income vs expenses vs
# profit per department), two pie charts (expense / income breakdown), and a
# profit trend line.
# ------------------------------------------------------------------------------------

def get_chart(filters):
	chart_type = filters.get("chart_type") or "Income vs Expenses"

	if chart_type == "Expense Breakdown":
		return get_expense_breakdown_chart(filters)
	if chart_type == "Income Breakdown":
		return get_income_breakdown_chart(filters)
	if chart_type == "Profit Trend":
		return get_profit_trend_chart(filters)

	return get_income_vs_expense_chart(filters)


def get_income_vs_expense_chart(filters):
	rows = frappe.get_all(
		"Forecast Form",
		filters=get_conditions(filters),
		fields=[
			"department",
			"sum(total_income) as total_income",
			"sum(total_expenses) as total_expenses",
		],
		group_by="department",
		order_by="department asc",
	)

	labels = [r.department or _("(No Department)") for r in rows]
	income_vals = [flt(r.total_income) for r in rows]
	expense_vals = [flt(r.total_expenses) for r in rows]
	profit_vals = [flt(r.total_income) - flt(r.total_expenses) for r in rows]

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Total Income"), "values": income_vals},
				{"name": _("Total Expenses"), "values": expense_vals},
				{"name": _("Profit / Loss"), "values": profit_vals},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 0},
		"colors": ["#2e7d32", "#e65100", "#1565c0"],
		"title": _("Income vs Expenses vs Profit by Department"),
	}


def get_expense_breakdown_chart(filters):
	rows, _raw_total = get_expense_breakdown(filters)
	if not rows:
		return {"data": {"labels": [], "datasets": []}, "type": "pie", "title": _("Expense Breakdown")}

	return {
		"data": {
			"labels": [r.label or _("Other") for r in rows],
			"datasets": [{"name": _("Expense"), "values": [flt(r.total) for r in rows]}],
		},
		"type": "pie",
		"title": _("Expense Breakdown by Expense Item"),
	}


def get_income_breakdown_chart(filters):
	rows, _raw_total = get_income_breakdown(filters)
	if not rows:
		return {"data": {"labels": [], "datasets": []}, "type": "pie", "title": _("Income Breakdown")}

	return {
		"data": {
			"labels": [r.label or _("Other") for r in rows],
			"datasets": [{"name": _("Revenue"), "values": [flt(r.total) for r in rows]}],
		},
		"type": "pie",
		"title": _("Income Breakdown by Service"),
	}


def get_profit_trend_chart(filters):
	rows = frappe.get_all(
		"Forecast Form",
		filters=get_conditions(filters),
		fields=["date", "total_income", "total_expenses"],
		order_by="date asc",
	)

	labels = [frappe.utils.formatdate(r.date) for r in rows]
	values = [flt(r.total_income) - flt(r.total_expenses) for r in rows]

	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Profit / Loss"), "values": values}],
		},
		"type": "line",
		"lineOptions": {"regionFill": 1},
		"colors": ["#1565c0"],
		"title": _("Profit / Loss Trend"),
	}


# ------------------------------------------------------------------------------------
# "Detailed" DASHBOARD:
#   - 3 summary cards: Total Income / Total Expenses / Net Profit
#   - Two side-by-side panels: Income Accounts / Expense Accounts
#   - Two donut charts (CSS conic-gradient, no external chart lib needed)
# Cards use parent totals; panels/donuts use reconciled category rows.
# ------------------------------------------------------------------------------------

PALETTE = [
	"#3b82f6", "#8b5cf6", "#22c55e", "#f97316", "#ec4899",
	"#14b8a6", "#ef4444", "#eab308", "#06b6d4", "#84cc16",
	"#a855f7", "#64748b",
]

UNALLOCATED_LABEL = "Unallocated"
UNALLOCATED_COLOR = "#cbd5e1"


def row_color(index, label):
	if label == _(UNALLOCATED_LABEL):
		return UNALLOCATED_COLOR
	return PALETTE[index % len(PALETTE)]


def get_income_rows(filters):
	parents = get_matching_parents(filters)
	if not parents:
		return []
	return frappe.db.sql(
		"""
		select service_income as label, sum(revenue) as total
		from `tab{child}`
		where parent in %(parents)s and parenttype = 'Forecast Form'
		group by service_income
		order by total desc
		""".format(child=CHILD_INCOME_DOCTYPE),
		{"parents": parents},
		as_dict=True,
	)


def get_expense_rows(filters):
	parents = get_matching_parents(filters)
	if not parents:
		return []
	return frappe.db.sql(
		"""
		select department_expenses as label, sum(expense) as total
		from `tab{child}`
		where parent in %(parents)s and parenttype = 'Forecast Form'
		group by department_expenses
		order by total desc
		""".format(child=CHILD_EXPENSE_DOCTYPE),
		{"parents": parents},
		as_dict=True,
	)


def build_conic_gradient(rows, total):
	"""Build a CSS conic-gradient() string from rows=[{label,total}] and a grand total."""
	if not total:
		return "#e5e7eb"
	cum = 0.0
	stops = []
	for i, r in enumerate(rows):
		pct = flt(r.total) / total * 100
		if pct < 0:
			continue
		color = row_color(i, r.label)
		start, cum = cum, cum + pct
		stops.append(f"{color} {start:.2f}% {cum:.2f}%")
	if not stops:
		return "#e5e7eb"
	return "conic-gradient(" + ", ".join(stops) + ")"


def render_donut(rows, total):
	gradient = build_conic_gradient(rows, total)
	return f"""
	<div style="position:relative;width:140px;height:140px;flex-shrink:0;">
		<div style="width:140px;height:140px;border-radius:50%;background:{gradient};"></div>
		<div style="position:absolute;top:20px;left:20px;width:100px;height:100px;
			border-radius:50%;background:#fff;"></div>
	</div>
	"""


def render_account_panel(title, rows, total, color):
	max_pct = max((abs(flt(r.total)) / total * 100 for r in rows), default=0) if total else 0
	max_pct = max_pct or 1
	row_html = ""
	for i, r in enumerate(rows):
		pct = flt(r.total) / total * 100 if total else 0
		bar_pct = min(abs(pct) / max_pct * 100, 100)
		dot_color = row_color(i, r.label)
		row_html += f"""
		<div style="display:flex;align-items:center;padding:5px 0;font-size:12px;gap:8px;">
			<span style="width:8px;height:8px;border-radius:50%;background:{dot_color};flex-shrink:0;"></span>
			<span style="flex:1;color:#334155;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{frappe.utils.escape_html(r.label or _("Other"))}</span>
			<span style="width:90px;text-align:right;font-weight:600;color:#0f172a;">{frappe.utils.fmt_money(r.total)}</span>
			<span style="width:42px;text-align:right;color:#94a3b8;">{pct:.1f}%</span>
			<span style="width:80px;height:6px;border-radius:3px;background:#f1f5f9;overflow:hidden;flex-shrink:0;">
				<span style="display:block;height:100%;width:{bar_pct:.1f}%;background:{dot_color};"></span>
			</span>
		</div>
		"""

	return f"""
	<div style="flex:1;min-width:260px;">
		<div style="display:flex;justify-content:space-between;align-items:center;
			border-bottom:1px solid #e2e8f0;padding-bottom:6px;margin-bottom:6px;">
			<span style="font-weight:600;color:#334155;">{title}</span>
			<span style="font-weight:700;color:{color};">{frappe.utils.fmt_money(total)}</span>
		</div>
		{row_html or '<div style="color:#94a3b8;font-size:12px;">' + _("No data") + '</div>'}
	</div>
	"""


def render_summary_card(label, value, color, note):
	return f"""
	<div style="flex:1;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px 18px;">
		<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;">{label}</div>
		<div style="font-size:22px;font-weight:700;color:{color};margin-top:4px;">{frappe.utils.fmt_money(value)}</div>
		<div style="font-size:11px;color:#94a3b8;margin-top:2px;">{note}</div>
	</div>
	"""


def render_reconciliation_note(income_gap, expense_gap):
	"""Only shown when child rows don't add up to the document totals, so the
	user can see exactly where the difference is instead of guessing."""
	if abs(income_gap) <= RECONCILE_TOLERANCE and abs(expense_gap) <= RECONCILE_TOLERANCE:
		return ""

	parts = []
	if abs(income_gap) > RECONCILE_TOLERANCE:
		parts.append(_("Income") + ": " + frappe.utils.fmt_money(income_gap))
	if abs(expense_gap) > RECONCILE_TOLERANCE:
		parts.append(_("Expenses") + ": " + frappe.utils.fmt_money(expense_gap))

	return f"""
	
	"""


def get_detailed_dashboard_html(filters):
	totals = get_parent_totals(filters)

	income_rows, income_raw_total = get_income_breakdown(filters, totals)
	expense_rows, expense_raw_total = get_expense_breakdown(filters, totals)

	total_income = totals.total_income
	total_expenses = totals.total_expenses
	net_profit = totals.net_profit
	net_color = "#16a34a" if net_profit >= 0 else "#dc2626"

	income_gap = total_income - income_raw_total
	expense_gap = total_expenses - expense_raw_total

	date_range = f"{frappe.utils.formatdate(filters.from_date)} - {frappe.utils.formatdate(filters.to_date)}"

	cards = f"""
	<div style="display:flex;gap:16px;margin-bottom:16px;">
		{render_summary_card(_("Total Income"), total_income, "#2563eb", _("From Forecast Forms"))}
		{render_summary_card(_("Total Expenses"), total_expenses, "#dc2626", _("From Forecast Forms"))}
		{render_summary_card(_("Net Profit / Loss"), net_profit, net_color, _("Net result"))}
	</div>
	"""

	income_panel = render_account_panel(_("Income Accounts"), income_rows, total_income, "#2563eb")
	expense_panel = render_account_panel(_("Expense Accounts"), expense_rows, total_expenses, "#dc2626")
	income_donut = render_donut(income_rows, total_income)
	expense_donut = render_donut(expense_rows, total_expenses)

	breakdown = f"""
	<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
		<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
			<span style="font-weight:600;color:#334155;">{_("P&L Accounts Breakdown")}</span>
			<span style="font-size:11px;color:#94a3b8;">{date_range}</span>
		</div>
		<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start;justify-content:center;">
			{income_panel}
			<div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
				{income_donut}
				<span style="font-size:11px;color:#94a3b8;">{_("Income Share")}</span>
			</div>
			<div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
				{expense_donut}
				<span style="font-size:11px;color:#94a3b8;">{_("Expense Share")}</span>
			</div>
			{expense_panel}
		</div>
	</div>
	"""

	note = render_reconciliation_note(income_gap, expense_gap)

	return f'<div style="font-family:inherit;">{cards}{note}{breakdown}</div>'


# ------------------------------------------------------------------------------------
# "Break-Even Analysis" DASHBOARD
#
# ASSUMPTION: ALL expenses are treated as Fixed Cost, so:
#   - Contribution per Patient = Revenue per Patient (ARPP)
#   - CM Ratio = 100%
#   - Break-even in Dollars = Total Expenses
#
# "Total Patients" = qty_per_month from the FIRST row (idx=1) of the Income child
# table on each Forecast Form, summed across all matching forms — this avoids
# double-counting the same patient base across multiple revenue lines.
#
# TARGET PROFIT (Formula Method):
#   Patients needed = (Target Profit + Total Expenses) / Contribution per Patient
# ------------------------------------------------------------------------------------

def get_total_patients_from_first_row(parents):
	"""Total Patients = qty_per_month of the FIRST income row (idx=1) on each
	Forecast Form, summed across the filtered forms."""
	if not parents:
		return 0
	rows = frappe.db.sql(
		"""
		select parent, qty_per_month
		from `tab{child}`
		where parent in %(parents)s and parenttype = 'Forecast Form' and idx = 1
		""".format(child=CHILD_INCOME_DOCTYPE),
		{"parents": parents},
		as_dict=True,
	)
	return sum(flt(r.qty_per_month) for r in rows)


def get_breakeven_metrics(filters):
	# Same control totals as every other view.
	totals = get_parent_totals(filters)
	total_income = totals.total_income
	total_expenses = totals.total_expenses
	profit = totals.net_profit

	parents = get_matching_parents(filters)
	total_patients = get_total_patients_from_first_row(parents)

	arpp = (total_income / total_patients) if total_patients else 0  # revenue per patient
	contribution_per_patient = arpp  # variable cost per patient assumed $0
	cm_ratio = 1.0 if total_income else 0  # 100% since variable cost = 0

	breakeven_patients = (total_expenses / contribution_per_patient) if contribution_per_patient else 0
	breakeven_dollars = total_expenses  # Fixed Cost / CM Ratio(1.0)

	margin_of_safety_patients = total_patients - breakeven_patients
	margin_of_safety_dollars = total_income - breakeven_dollars  # == profit

	operating_leverage = (total_income / profit) if profit else 0  # Total CM / Profit
	cost_per_patient = (total_expenses / total_patients) if total_patients else 0

	investment = flt(filters.get("investment_amount"))
	roi_pct = (profit / investment * 100) if investment else None

	# ---- Payback period -----------------------------------------------------
	# "Forecast Profit Is" (profit_basis) tells us what one period of profit
	# represents — Annual or Monthly. Everything is normalised to a monthly
	# profit first, then presented in whichever unit the user picked in
	# "Show Payback In" (payback_unit). Without this, dividing investment by
	# a monthly profit and labelling the answer "years" is off by 12x.
	profit_basis = filters.get("profit_basis") or "Monthly"
	payback_unit = filters.get("payback_unit") or "Years"

	monthly_profit = (profit / 12.0) if profit_basis == "Annual" else profit
	annual_profit = profit if profit_basis == "Annual" else profit * 12.0

	payback_months = (investment / monthly_profit) if (investment and monthly_profit > 0) else None
	payback_years = (payback_months / 12.0) if payback_months is not None else None
	years_to_recover = payback_years  # kept for backwards compatibility

	# ---- Target Profit (Formula Method) -------------------------------------
	target_profit = flt(filters.get("target_profit_amount"))
	target_profit_patients = (
		(target_profit + total_expenses) / contribution_per_patient
		if (target_profit and contribution_per_patient) else 0
	)
	target_profit_revenue = (target_profit + total_expenses) if target_profit else 0
	additional_patients_needed = target_profit_patients - total_patients if target_profit else 0

	return frappe._dict(
		total_income=total_income,
		total_expenses=total_expenses,
		profit=profit,
		total_patients=total_patients,
		arpp=arpp,
		contribution_per_patient=contribution_per_patient,
		cm_ratio=cm_ratio,
		breakeven_patients=breakeven_patients,
		breakeven_dollars=breakeven_dollars,
		margin_of_safety_patients=margin_of_safety_patients,
		margin_of_safety_dollars=margin_of_safety_dollars,
		operating_leverage=operating_leverage,
		cost_per_patient=cost_per_patient,
		investment=investment,
		roi_pct=roi_pct,
		years_to_recover=years_to_recover,
		profit_basis=profit_basis,
		payback_unit=payback_unit,
		monthly_profit=monthly_profit,
		annual_profit=annual_profit,
		payback_months=payback_months,
		payback_years=payback_years,
		target_profit=target_profit,
		target_profit_patients=target_profit_patients,
		target_profit_revenue=target_profit_revenue,
		additional_patients_needed=additional_patients_needed,
	)


def render_metric_card(label, value_html, color, note=""):
	return f"""
	<div style="flex:1;min-width:150px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;">
		<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;">{label}</div>
		<div style="font-size:20px;font-weight:700;color:{color};margin-top:4px;">{value_html}</div>
		{f'<div style="font-size:11px;color:#94a3b8;margin-top:2px;">{note}</div>' if note else ''}
	</div>
	"""


def format_payback(m):
	"""Return (main value, note). The main value uses the unit the user picked
	in the filters; the note repeats the same figure in the other unit."""
	if not m.investment:
		return "-", ""
	if m.payback_months is None:
		return _("N/A (no profit)"), ""

	if m.payback_unit == "Months":
		value = f"{m.payback_months:,.1f} " + _("months")
		note = "≈ " + f"{m.payback_years:,.1f} " + _("years")
	else:
		value = f"{m.payback_years:,.1f} " + _("years")
		note = "≈ " + f"{m.payback_months:,.0f} " + _("months")

	return value, note


def render_breakeven_bar(m):
	"""Horizontal bar: fixed-cost zone (red) vs margin-of-safety zone (green), with an actual-patients marker."""
	bar_max = max(m.total_patients, m.breakeven_patients, 1) * 1.15
	breakeven_pct = min(m.breakeven_patients / bar_max * 100, 100)
	actual_pct = min(m.total_patients / bar_max * 100, 100)
	actual_color = "#16a34a" if m.total_patients >= m.breakeven_patients else "#dc2626"

	return f"""
	<div style="margin-top:18px;">
		<div style="position:relative;height:28px;background:#fee2e2;border-radius:6px;overflow:visible;">
			<div style="position:absolute;left:0;top:0;bottom:0;width:{actual_pct:.1f}%;
				background:{actual_color};border-radius:6px 0 0 6px;transition:width .3s;"></div>
			<div style="position:absolute;left:{breakeven_pct:.1f}%;top:-4px;bottom:-4px;width:2px;background:#0f172a;"></div>
			<div style="position:absolute;left:{breakeven_pct:.1f}%;top:-22px;transform:translateX(-50%);
				font-size:10px;color:#0f172a;white-space:nowrap;">{_("Break-even")}: {m.breakeven_patients:,.0f}</div>
		</div>
		<div style="display:flex;justify-content:space-between;font-size:11px;color:#94a3b8;margin-top:6px;">
			<span>0</span>
			<span>{_("Actual")}: {m.total_patients:,.0f} {_("patients")}</span>
			<span>{bar_max:,.0f}</span>
		</div>
	</div>
	"""


def render_self_sufficient_badge(m):
	is_self_sufficient = m.profit >= 0 and m.total_patients >= m.breakeven_patients

	if is_self_sufficient:
		bg, border, text_color = "#dcfce7", "#86efac", "#15803d"
		label = _("Self-sufficient")
		detail = _("Covers its own costs") + f" — {_('surplus')} " + frappe.utils.fmt_money(m.profit)
	else:
		bg, border, text_color = "#fee2e2", "#fca5a5", "#b91c1c"
		label = _("Not self-sufficient")
		shortfall = m.breakeven_patients - m.total_patients
		if m.profit < 0:
			detail = _("Needs additional funding") + f" — {_('shortfall')} " + frappe.utils.fmt_money(abs(m.profit))
		else:
			detail = _("Needs additional funding") + f" — {shortfall:,.0f} " + _("more patients needed")

	return f"""
	<div style="display:inline-flex;align-items:center;gap:10px;background:{bg};border:1px solid {border};
		border-radius:8px;padding:8px 16px;margin-bottom:14px;">
		<span style="width:10px;height:10px;border-radius:50%;background:{text_color};flex-shrink:0;"></span>
		<span style="font-weight:700;font-size:13px;color:{text_color};">{label}</span>
		<span style="font-size:12px;color:{text_color};opacity:0.85;">{detail}</span>
	</div>
	"""


def get_breakeven_dashboard_html(filters):
	m = get_breakeven_metrics(filters)
	date_range = f"{frappe.utils.formatdate(filters.from_date)} - {frappe.utils.formatdate(filters.to_date)}"
	mos_color = "#16a34a" if m.margin_of_safety_patients >= 0 else "#dc2626"

	self_sufficient_badge = render_self_sufficient_badge(m)

	assumption_note = f"""
	<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 14px;
		font-size:12px;color:#92400e;margin-bottom:14px;">
		{_("Assumption: all expenses are treated as Fixed Cost, so variable cost per patient = $0 and CM Ratio = 100%. Break-even patients = Total Expenses / Revenue per Patient.")}
	</div>
	"""

	totals_strip = f"""
	<div style="display:flex;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
		{render_metric_card(_("Total Income"), frappe.utils.fmt_money(m.total_income), "#2563eb")}
		{render_metric_card(_("Total Expenses"), frappe.utils.fmt_money(m.total_expenses), "#dc2626")}
		{render_metric_card(_("Net Profit / Loss"), frappe.utils.fmt_money(m.profit), "#16a34a" if m.profit >= 0 else "#dc2626")}
	</div>
	"""

	cards_row1 = f"""
	<div style="display:flex;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
		{render_metric_card(_("Total Patients"), f"{m.total_patients:,.0f}", "#0f172a")}
		{render_metric_card(_("Break-even Patients"), f"{m.breakeven_patients:,.0f}", "#dc2626", _("Needed to cover fixed costs"))}
		{render_metric_card(_("Margin of Safety"), f"{m.margin_of_safety_patients:,.0f} " + _("patients"), mos_color, frappe.utils.fmt_money(m.margin_of_safety_dollars))}
		{render_metric_card(_("Revenue / Patient (ARPP)"), frappe.utils.fmt_money(m.arpp), "#2563eb")}
	</div>
	"""

	cards_row2 = f"""
	<div style="display:flex;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
		{render_metric_card(_("Cost / Patient"), frappe.utils.fmt_money(m.cost_per_patient), "#f97316")}
		{render_metric_card(_("CM Ratio"), f"{m.cm_ratio * 100:.0f}%", "#8b5cf6")}
		{render_metric_card(_("Break-even Revenue"), frappe.utils.fmt_money(m.breakeven_dollars), "#dc2626")}
		{render_metric_card(_("Operating Leverage"), f"{m.operating_leverage:.2f}x" if m.operating_leverage else "-", "#0f172a", _("Profit sensitivity to volume change"))}
	</div>
	"""

	roi_card = ""
	if m.investment:
		roi_value = f"{m.roi_pct:.1f}%" if m.roi_pct is not None else "-"
		roi_note = frappe.utils.fmt_money(m.investment) + " " + _("invested")
		payback_value, payback_note = format_payback(m)
		roi_card = f"""
		<div style="display:flex;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
			{render_metric_card(_("ROI"), roi_value, "#16a34a" if (m.roi_pct or 0) >= 0 else "#dc2626", roi_note)}
			{render_metric_card(_("Payback Period"), payback_value, "#0f172a", payback_note)}
		</div>
		"""

	bar = render_breakeven_bar(m)

	return f"""
	<div style="font-family:inherit;">
		<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
			<span style="font-weight:600;color:#334155;font-size:14px;">{_("Break-Even Analysis")}</span>
			<span style="font-size:11px;color:#94a3b8;">{date_range}</span>
		</div>
		{self_sufficient_badge}
		{assumption_note}
		{totals_strip}
		{cards_row1}
		{cards_row2}
		{roi_card}
		<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
			<div style="font-weight:600;color:#334155;font-size:12px;margin-bottom:4px;">{_("Actual Patients vs Break-even")}</div>
			{bar}
		</div>
	</div>
	"""


# ------------------------------------------------------------------------------------
# "Target Profit Analysis" DASHBOARD
#   Patients needed = (Target Profit + Total Expenses) / Contribution per Patient
#   (Break-even is the special case where Target Profit = 0.)
# ------------------------------------------------------------------------------------

def render_target_profit_bar(m):
	"""Horizontal bar: fixed-cost zone vs target-profit zone, with an actual-patients marker."""
	bar_max = max(m.total_patients, m.target_profit_patients, 1) * 1.15
	target_pct = min(m.target_profit_patients / bar_max * 100, 100)
	actual_pct = min(m.total_patients / bar_max * 100, 100)
	actual_color = "#16a34a" if m.total_patients >= m.target_profit_patients else "#7c3aed"

	return f"""
	<div style="margin-top:18px;">
		<div style="position:relative;height:28px;background:#ede9fe;border-radius:6px;overflow:visible;">
			<div style="position:absolute;left:0;top:0;bottom:0;width:{actual_pct:.1f}%;
				background:{actual_color};border-radius:6px 0 0 6px;transition:width .3s;"></div>
			<div style="position:absolute;left:{target_pct:.1f}%;top:-4px;bottom:-4px;width:2px;background:#0f172a;"></div>
			<div style="position:absolute;left:{target_pct:.1f}%;top:-22px;transform:translateX(-50%);
				font-size:10px;color:#0f172a;white-space:nowrap;">{_("Target profit")}: {m.target_profit_patients:,.0f}</div>
		</div>
		<div style="display:flex;justify-content:space-between;font-size:11px;color:#94a3b8;margin-top:6px;">
			<span>0</span>
			<span>{_("Actual")}: {m.total_patients:,.0f} {_("patients")}</span>
			<span>{bar_max:,.0f}</span>
		</div>
	</div>
	"""


def render_target_profit_status_badge(m):
	reached = m.total_patients >= m.target_profit_patients

	if reached:
		bg, border, text_color = "#dcfce7", "#86efac", "#15803d"
		label = _("Target already met")
		detail = f"{m.total_patients - m.target_profit_patients:,.0f} " + _("surplus patients")
	else:
		bg, border, text_color = "#f5f3ff", "#ddd6fe", "#7c3aed"
		label = _("Target not yet met")
		detail = f"{m.additional_patients_needed:,.0f} " + _("more patients needed")

	return f"""
	<div style="display:inline-flex;align-items:center;gap:10px;background:{bg};border:1px solid {border};
		border-radius:8px;padding:8px 16px;margin-bottom:14px;">
		<span style="width:10px;height:10px;border-radius:50%;background:{text_color};flex-shrink:0;"></span>
		<span style="font-weight:700;font-size:13px;color:{text_color};">{label}</span>
		<span style="font-size:12px;color:{text_color};opacity:0.85;">{detail}</span>
	</div>
	"""


def get_target_profit_dashboard_html(filters):
	m = get_breakeven_metrics(filters)
	date_range = f"{frappe.utils.formatdate(filters.from_date)} - {frappe.utils.formatdate(filters.to_date)}"

	if not m.target_profit:
		return f"""
		<div style="font-family:inherit;">
			<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px 16px;
				font-size:13px;color:#92400e;">
				{_("Enter a Target Profit amount in the filters above to run the Formula Method calculation.")}
			</div>
		</div>
		"""

	formula_row = f"""
	<div style="display:flex;align-items:center;gap:10px;background:#f8fafc;border-radius:6px;
		padding:10px 14px;margin-bottom:14px;font-size:13px;color:#334155;flex-wrap:wrap;">
		<span>{_("Patients needed")} =</span>
		<span style="display:inline-flex;flex-direction:column;align-items:center;font-weight:600;">
			<span style="border-bottom:1px solid #334155;padding:0 4px;">{_("Target Profit")} + {_("Fixed Expenses")}</span>
			<span style="padding:0 4px;">{_("Contribution per Patient")}</span>
		</span>
		<span>=</span>
		<span style="display:inline-flex;flex-direction:column;align-items:center;font-weight:600;">
			<span style="border-bottom:1px solid #334155;padding:0 4px;">{frappe.utils.fmt_money(m.target_profit)} + {frappe.utils.fmt_money(m.total_expenses)}</span>
			<span style="padding:0 4px;">{frappe.utils.fmt_money(m.contribution_per_patient)}</span>
		</span>
		<span>=</span>
		<span style="font-weight:700;color:#7c3aed;">{m.target_profit_patients:,.0f}</span>
	</div>
	"""

	cards = f"""
	<div style="display:flex;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
		{render_metric_card(_("Target Profit"), frappe.utils.fmt_money(m.target_profit), "#7c3aed")}
		{render_metric_card(_("Patients Needed"), f"{m.target_profit_patients:,.0f}", "#7c3aed", _("To attain target profit"))}
		{render_metric_card(_("Revenue Needed"), frappe.utils.fmt_money(m.target_profit_revenue), "#7c3aed")}
		{render_metric_card(_("Actual Patients"), f"{m.total_patients:,.0f}", "#0f172a")}
	</div>
	"""

	badge = render_target_profit_status_badge(m)
	bar = render_target_profit_bar(m)

	return f"""
	<div style="font-family:inherit;">
		<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
			<span style="font-weight:600;color:#334155;font-size:14px;">{_("Target Profit Analysis")}</span>
			<span style="font-size:11px;color:#94a3b8;">{date_range}</span>
		</div>
		{badge}
		<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 14px;
			font-size:12px;color:#92400e;margin-bottom:14px;">
			{_("Formula Method: assumes all expenses are Fixed Cost, so Contribution per Patient = Revenue per Patient (CM Ratio = 100%).")}
		</div>
		{formula_row}
		{cards}
		<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
			<div style="font-weight:600;color:#334155;font-size:12px;margin-bottom:4px;">{_("Actual Patients vs Target Profit Requirement")}</div>
			{bar}
		</div>
	</div>
	"""