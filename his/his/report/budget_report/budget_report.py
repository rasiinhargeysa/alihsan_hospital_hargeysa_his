# Copyright (c) 2026, Rasiin Tech and contributors
# For license information, please see license.txt

import datetime

import frappe
from frappe import _
from frappe.utils import flt, formatdate

from erpnext.controllers.trends import get_period_date_ranges, get_period_month_ranges


def execute(filters=None):
	filters = normalize_filters(filters)

	columns = get_columns(filters)

	if filters.get("budget_against_filter"):
		dimensions = filters.get("budget_against_filter")
	else:
		dimensions = get_dimensions(filters)

	period_month_ranges = get_period_month_ranges(filters.period, filters.from_fiscal_year)
	cam_map = get_dimension_account_month_map(filters)

	accounts = set()
	for dimension_items in cam_map.values():
		accounts.update(dimension_items.keys())

	account_root_type_map = get_account_root_type_map(accounts)

	raw_data = []
	for dimension in dimensions:
		dimension_items = cam_map.get(dimension, {})
		if dimension_items:
			raw_data = get_final_data(
				dimension,
				dimension_items,
				filters,
				period_month_ranges,
				raw_data,
				0,
				account_root_type_map,
			)

	data = build_profit_and_loss_view(raw_data, filters)
	chart = get_chart_data(filters, data)

	return columns, data, None, chart


def normalize_filters(filters):
	filters = frappe._dict(filters or {})

	if not filters.get("from_fiscal_year"):
		filters.from_fiscal_year = frappe.defaults.get_user_default("fiscal_year")

	if not filters.get("to_fiscal_year"):
		filters.to_fiscal_year = filters.from_fiscal_year

	if not filters.get("period"):
		filters.period = "Yearly"

	if not filters.get("budget_against"):
		filters.budget_against = "Cost Center"

	if not filters.get("show_cumulative"):
		filters.show_cumulative = 0

	if not filters.get("budget_against_filter"):
		filters.budget_against_filter = []

	return filters


def get_dimensions(filters):
	order_by = ""
	if filters.get("budget_against") == "Cost Center":
		order_by = "order by lft"

	if filters.get("budget_against") in ["Cost Center", "Project"]:
		return frappe.db.sql_list(
			"""
			select
				name
			from
				`tab{tab}`
			where
				company = %s
			{order_by}
			""".format(tab=filters.get("budget_against"), order_by=order_by),
			filters.get("company"),
		)
	else:
		return frappe.db.sql_list(
			"""
			select
				name
			from
				`tab{tab}`
			""".format(tab=filters.get("budget_against"))
		)  # nosec


def get_account_root_type_map(accounts):
	if not accounts:
		return {}

	account_details = frappe.get_all(
		"Account",
		filters={"name": ["in", list(accounts)]},
		fields=["name", "root_type"],
	)

	return {d.name: d.root_type for d in account_details}


def get_final_data(
	dimension,
	dimension_items,
	filters,
	period_month_ranges,
	data,
	dcc_allocation,
	account_root_type_map,
):
	numeric_fields = get_numeric_fieldnames(filters)

	for account, monthwise_data in dimension_items.items():
		root_type = account_root_type_map.get(account)

		# Profit & Loss only
		if root_type not in ("Income", "Expense"):
			continue

		row = {
			"budget_against": dimension,
			"particulars": account,
			"account": account,
			"root_type": root_type,
			"is_group": 0,
			"is_total": 0,
			"is_net": 0,
			"indent": 1,
			"parent_particulars": root_type,
		}

		period_totals = []
		grand_budget = 0
		grand_actual = 0
		grand_variance = 0

		for year in get_fiscal_years(filters):
			last_total = 0
			for relevant_months in period_month_ranges:
				period_budget = 0
				period_actual = 0

				for month in relevant_months:
					if monthwise_data.get(year[0]):
						month_data = monthwise_data.get(year[0]).get(month, {})
						period_budget += flt(month_data.get("target"))
						period_actual += flt(month_data.get("actual"))

				period_budget += last_total

				if dcc_allocation:
					period_budget = period_budget * (dcc_allocation / 100)
					period_actual = period_actual * (dcc_allocation / 100)

				if filters.get("show_cumulative"):
					last_total = period_budget - period_actual

				period_variance = period_actual - period_budget

				period_totals.extend([period_budget, period_actual, period_variance])

				grand_budget += period_budget
				grand_actual += period_actual
				grand_variance += period_variance

		for idx, fieldname in enumerate(numeric_fields):
			row[fieldname] = period_totals[idx] if idx < len(period_totals) else 0

		if filters.period != "Yearly":
			row["total_budget"] = grand_budget
			row["total_actual"] = grand_actual
			row["total_variance"] = grand_variance

		data.append(row)

	return data


def build_profit_and_loss_view(raw_data, filters):
	if not raw_data:
		return []

	numeric_fields = get_numeric_fieldnames(filters)
	if filters.period != "Yearly":
		numeric_fields += ["total_budget", "total_actual", "total_variance"]

	income_rows = []
	expense_rows = []

	for row in raw_data:
		if row.get("root_type") == "Income":
			row["indent"] = 1
			row["parent_particulars"] = "Income"
			income_rows.append(row)
		elif row.get("root_type") == "Expense":
			row["indent"] = 1
			row["parent_particulars"] = "Expense"
			expense_rows.append(row)

	final_data = []

	def make_empty_row(
		label,
		root_type="",
		is_group=0,
		is_total=0,
		is_net=0,
		indent=0,
		parent_particulars="",
	):
		row = {
			"budget_against": "",
			"particulars": label,
			"account": "",
			"root_type": root_type,
			"is_group": is_group,
			"is_total": is_total,
			"is_net": is_net,
			"indent": indent,
			"parent_particulars": parent_particulars,
		}
		for f in numeric_fields:
			row[f] = 0
		return row

	def accumulate(rows):
		total = {f: 0 for f in numeric_fields}
		for r in rows:
			for f in numeric_fields:
				total[f] += flt(r.get(f))
		return total

	# Income
	income_header = make_empty_row("Income", "Income", is_group=1, indent=0)
	final_data.append(income_header)

	income_values = accumulate(income_rows)
	final_data.extend(income_rows)

	income_total = make_empty_row("Total Income", "Income", is_total=1, indent=0)
	for f in numeric_fields:
		income_total[f] = income_values[f]
	final_data.append(income_total)

	# Expense
	expense_header = make_empty_row("Expense", "Expense", is_group=1, indent=0)
	final_data.append(expense_header)

	expense_values = accumulate(expense_rows)
	final_data.extend(expense_rows)

	expense_total = make_empty_row("Total Expense", "Expense", is_total=1, indent=0)
	for f in numeric_fields:
		expense_total[f] = expense_values[f]
	final_data.append(expense_total)

	# Net
	net_row = make_empty_row("Net Profit / Loss", is_net=1, indent=0)
	for f in numeric_fields:
		net_row[f] = flt(income_values.get(f)) - flt(expense_values.get(f))
	final_data.append(net_row)

	return final_data


def get_columns(filters):
	columns = [
		{
			"label": _("Parent Particulars"),
			"fieldname": "parent_particulars",
			"fieldtype": "Data",
			"hidden": 1,
		},
		{
			"label": _("Particulars"),
			"fieldname": "particulars",
			"fieldtype": "Data",
			"width": 320,
		},

	]

	for col in get_period_column_meta(filters):
		columns.append(col)

	if filters.period != "Yearly":
		columns.extend(
			[
				{
					"label": _("Total Budget"),
					"fieldname": "total_budget",
					"fieldtype": "Float",
					"width": 150,
				},
				{
					"label": _("Total Actual"),
					"fieldname": "total_actual",
					"fieldtype": "Float",
					"width": 150,
				},
				{
					"label": _("Total Variance"),
					"fieldname": "total_variance",
					"fieldtype": "Float",
					"width": 150,
				},
			]
		)

	return columns


def get_period_column_meta(filters):
	columns = []
	group_months = False if filters.period == "Monthly" else True

	for year in get_fiscal_years(filters):
		period_index = 1
		for from_date, to_date in get_period_date_ranges(filters.period, year[0]):
			if filters.period == "Yearly":
				columns.extend(
					[
						{
							"label": _("Budget") + " " + str(year[0]),
							"fieldname": f"budget_{year[0]}",
							"fieldtype": "Float",
							"width": 150,
						},
						{
							"label": _("Actual") + " " + str(year[0]),
							"fieldname": f"actual_{year[0]}",
							"fieldtype": "Float",
							"width": 150,
						},
						{
							"label": _("Variance") + " " + str(year[0]),
							"fieldname": f"variance_{year[0]}",
							"fieldtype": "Float",
							"width": 150,
						},
					]
				)
			else:
				if group_months:
					period_label = (
						formatdate(from_date, format_string="MMM")
						+ "-"
						+ formatdate(to_date, format_string="MMM")
					)
				else:
					period_label = formatdate(from_date, format_string="MMM")

				columns.extend(
					[
						{
							"label": _("Budget") + f" ({period_label}) " + str(year[0]),
							"fieldname": f"budget_{year[0]}_{period_index}",
							"fieldtype": "Float",
							"width": 150,
						},
						{
							"label": _("Actual") + f" ({period_label}) " + str(year[0]),
							"fieldname": f"actual_{year[0]}_{period_index}",
							"fieldtype": "Float",
							"width": 150,
						},
						{
							"label": _("Variance") + f" ({period_label}) " + str(year[0]),
							"fieldname": f"variance_{year[0]}_{period_index}",
							"fieldtype": "Float",
							"width": 150,
						},
					]
				)

			period_index += 1

	return columns


def get_numeric_fieldnames(filters):
	fieldnames = []

	for year in get_fiscal_years(filters):
		period_index = 1
		for from_date, to_date in get_period_date_ranges(filters.period, year[0]):
			if filters.period == "Yearly":
				fieldnames.extend(
					[
						f"budget_{year[0]}",
						f"actual_{year[0]}",
						f"variance_{year[0]}",
					]
				)
			else:
				fieldnames.extend(
					[
						f"budget_{year[0]}_{period_index}",
						f"actual_{year[0]}_{period_index}",
						f"variance_{year[0]}_{period_index}",
					]
				)
			period_index += 1

	return fieldnames


def get_dimension_target_details(filters):
	budget_against = frappe.scrub(filters.get("budget_against"))
	cond = ""

	params = [
		filters.from_fiscal_year,
		filters.to_fiscal_year,
		filters.budget_against,
		filters.company,
	]

	if filters.get("budget_against_filter"):
		cond += """ and b.{budget_against} in ({placeholders})""".format(
			budget_against=budget_against,
			placeholders=", ".join(["%s"] * len(filters.get("budget_against_filter"))),
		)
		params.extend(filters.get("budget_against_filter"))

	return frappe.db.sql(
		"""
			select
				b.{budget_against} as budget_against,
				b.monthly_distribution,
				ba.account,
				ba.budget_amount,
				b.fiscal_year
			from
				`tabBudget` b,
				`tabBudget Account` ba
			where
				b.name = ba.parent
				and b.docstatus = 1
				and b.fiscal_year between %s and %s
				and b.budget_against = %s
				and b.company = %s
				{cond}
			order by
				b.fiscal_year, ba.account
		""".format(budget_against=budget_against, cond=cond),
		tuple(params),
		as_dict=True,
	)


def get_target_distribution_details(filters):
	target_details = {}

	for d in frappe.db.sql(
		"""
			select
				md.name,
				mdp.month,
				mdp.percentage_allocation
			from
				`tabMonthly Distribution Percentage` mdp,
				`tabMonthly Distribution` md
			where
				mdp.parent = md.name
				and md.fiscal_year between %s and %s
			order by
				md.fiscal_year
		""",
		(filters.from_fiscal_year, filters.to_fiscal_year),
		as_dict=1,
	):
		target_details.setdefault(d.name, {}).setdefault(d.month, flt(d.percentage_allocation))

	return target_details


def get_all_actual_details(filters, dimension_names):
	"""
	Fetch GL actual amounts for ALL relevant dimension items (cost centers / projects)
	in a single query, instead of one query per dimension item.

	Returns a nested dict:
		{ budget_against_name: { account: { fiscal_year: { month_name: amount } } } }
	"""
	if not dimension_names:
		return {}

	budget_against = frappe.scrub(filters.get("budget_against"))

	# Restrict to the exact set of GL "budget_against" values relevant to this report,
	# instead of running a correlated EXISTS subquery per row.
	gl_dimension_values = set(dimension_names)

	if filters.get("budget_against") == "Cost Center":
		# Expand each cost center filter to include all of its descendants via lft/rgt,
		# fetched in ONE query instead of one query per dimension item.
		cc_ranges = frappe.get_all(
			"Cost Center",
			filters={"name": ["in", list(dimension_names)]},
			fields=["name", "lft", "rgt"],
		)

		if cc_ranges:
			range_conditions = " or ".join(
				["(lft >= {0} and rgt <= {1})".format(flt(r.lft), flt(r.rgt)) for r in cc_ranges]
			)
			descendant_names = frappe.db.sql_list(
				"""
					select name from `tabCost Center`
					where {conditions}
				""".format(conditions=range_conditions)
			)
			gl_dimension_values.update(descendant_names)

	if not gl_dimension_values:
		return {}

	placeholders = ", ".join(["%s"] * len(gl_dimension_values))

	ac_details = frappe.db.sql(
		"""
			select
				gl.account,
				gl.debit,
				gl.credit,
				gl.fiscal_year,
				MONTHNAME(gl.posting_date) as month_name,
				gl.{budget_against} as budget_against
			from
				`tabGL Entry` gl
			where
				gl.is_cancelled = 0
				and gl.fiscal_year between %s and %s
				and gl.{budget_against} in ({placeholders})
		""".format(budget_against=budget_against, placeholders=placeholders),
		tuple([filters.from_fiscal_year, filters.to_fiscal_year] + list(gl_dimension_values)),
		as_dict=1,
	)

	# Build lookup: actual_map[budget_against][account][fiscal_year][month] = net amount
	actual_map = {}
	for d in ac_details:
		bucket = (
			actual_map.setdefault(d.budget_against, {})
			.setdefault(d.account, {})
			.setdefault(d.fiscal_year, {})
		)
		bucket[d.month_name] = bucket.get(d.month_name, 0) + flt(d.debit) - flt(d.credit)

	# If budget_against is Cost Center, GL entries posted against *descendant* cost centers
	# need to roll up into their ancestor budget's totals. Map descendants back to the
	# budgeted ancestor cost center.
	if filters.get("budget_against") == "Cost Center" and cc_ranges:
		# Build name -> (lft, rgt) for descendants we queried, and ancestor ranges.
		all_cc = frappe.get_all(
			"Cost Center",
			filters={"name": ["in", list(gl_dimension_values)]},
			fields=["name", "lft", "rgt"],
		)
		cc_lft_rgt = {c.name: (c.lft, c.rgt) for c in all_cc}

		rolled_up = {}
		for ancestor in cc_ranges:
			a_lft, a_rgt = ancestor.lft, ancestor.rgt
			for child_name, child_data in actual_map.items():
				c_lft, c_rgt = cc_lft_rgt.get(child_name, (None, None))
				if c_lft is None:
					continue
				if c_lft >= a_lft and c_rgt <= a_rgt:
					ancestor_bucket = rolled_up.setdefault(ancestor.name, {})
					for account, fy_data in child_data.items():
						acc_bucket = ancestor_bucket.setdefault(account, {})
						for fy, month_data in fy_data.items():
							fy_bucket = acc_bucket.setdefault(fy, {})
							for month, amount in month_data.items():
								fy_bucket[month] = fy_bucket.get(month, 0) + amount
		actual_map = rolled_up

	return actual_map


def get_dimension_account_month_map(filters):
	dimension_target_details = get_dimension_target_details(filters)
	tdd = get_target_distribution_details(filters)

	# Collect the distinct set of dimension names (cost centers / projects) that
	# actually have budgets, so we query GL Entry once for exactly that set.
	dimension_names = {ccd.budget_against for ccd in dimension_target_details}
	actual_map = get_all_actual_details(filters, dimension_names)

	cam_map = {}

	for ccd in dimension_target_details:
		dimension_actuals = actual_map.get(ccd.budget_against, {}).get(ccd.account, {})

		for month_id in range(1, 13):
			month = datetime.date(2013, month_id, 1).strftime("%B")

			cam_map.setdefault(ccd.budget_against, {}).setdefault(ccd.account, {}).setdefault(
				ccd.fiscal_year, {}
			).setdefault(month, frappe._dict({"target": 0.0, "actual": 0.0}))

			tav_dict = cam_map[ccd.budget_against][ccd.account][ccd.fiscal_year][month]

			month_percentage = (
				tdd.get(ccd.monthly_distribution, {}).get(month, 0)
				if ccd.monthly_distribution
				else 100.0 / 12
			)

			tav_dict.target = flt(ccd.budget_amount) * month_percentage / 100

			actual_amount = dimension_actuals.get(ccd.fiscal_year, {}).get(month, 0)
			tav_dict.actual += actual_amount

			if tav_dict.actual < 0:
				tav_dict.actual = -tav_dict.actual

	return cam_map


def get_fiscal_years(filters):
	return frappe.db.sql(
		"""
			select
				name
			from
				`tabFiscal Year`
			where
				name between %(from_fiscal_year)s and %(to_fiscal_year)s
			order by
				name
		""",
		{
			"from_fiscal_year": filters["from_fiscal_year"],
			"to_fiscal_year": filters["to_fiscal_year"],
		},
	)


def get_chart_data(filters, data):
	if not data:
		return None

	labels = []
	group_months = False if filters.period == "Monthly" else True

	for year in get_fiscal_years(filters):
		for from_date, to_date in get_period_date_ranges(filters.period, year[0]):
			if filters.period == "Yearly":
				labels.append(year[0])
			else:
				if group_months:
					labels.append(
						formatdate(from_date, format_string="MMM")
						+ "-"
						+ formatdate(to_date, format_string="MMM")
					)
				else:
					labels.append(formatdate(from_date, format_string="MMM"))

	no_of_columns = len(labels)

	budget_values = [0] * no_of_columns
	actual_values = [0] * no_of_columns

	usable_rows = [
		d for d in data if not d.get("is_group") and not d.get("is_total") and not d.get("is_net")
	]

	fieldnames = get_numeric_fieldnames(filters)

	for d in usable_rows:
		index = 0
		for i in range(no_of_columns):
			budget_values[i] += flt(d.get(fieldnames[index]))
			actual_values[i] += flt(d.get(fieldnames[index + 1]))
			index += 3

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Budget"), "chartType": "bar", "values": budget_values},
				{"name": _("Actual"), "chartType": "bar", "values": actual_values},
			],
		},
		"type": "bar",
	}