# Copyright (c) 2023, Rasiin Tech and contributors
# For license information, please see license.txt
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _, msgprint
from frappe.utils import flt

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
	get_dimension_with_children,
)


def execute(filters=None):
	return _execute(filters)


def _execute(filters, additional_table_columns=None, additional_query_columns=None):
	if not filters:
		filters = frappe._dict({})

	invoice_list = get_invoices(filters, additional_query_columns)
	columns = get_columns(additional_table_columns)

	if not invoice_list:
		msgprint(_("No record found"))
		return columns, []

	income_accounts = get_income_accounts(invoice_list)
	invoice_income_map = get_invoice_income_map(invoice_list)
	invoice_income_map, invoice_tax_map = get_invoice_tax_map(
		invoice_list, invoice_income_map, income_accounts
	)
	invoice_so_dn_map = get_invoice_so_dn_map(invoice_list)
	mode_of_payments = get_mode_of_payments([inv.name for inv in invoice_list])
	item_details_by_account = get_item_details_by_account(invoice_list)

	data = {}

	for inv in invoice_list:
		income_map = invoice_income_map.get(inv.name, {})

		if not income_map:
			continue

		# Total net amount recorded against income accounts on this invoice.
		# Used to proportionally split invoice-level totals (tax, grand total,
		# cash, credit) across each income account touched by the invoice, so
		# that summing a column across all accounts reproduces the invoice total.
		total_net_for_invoice = sum(flt(v) for v in income_map.values())
		invoice_tax_total = sum(flt(v) for v in invoice_tax_map.get(inv.name, {}).values())
		mop_set = set(mode_of_payments.get(inv.name, []))
		so_dn_map = invoice_so_dn_map.get(inv.name, {})

		for income_account, amount in income_map.items():
			amount = flt(amount)

			if income_account not in data:
				data[income_account] = {
					"income_account": income_account,
					"no_of_invoices": 0,
					"net_total": 0,
					"tax_total": 0,
					"grand_total": 0,
					"rounded_total": 0,
					"outstanding_amount": 0,
					"cash": 0,
					"sales_order": set(),
					"delivery_note": set(),
					"cost_center": set(),
					"warehouse": set(),
					"mode_of_payment": set(),
				}

			# Share of this invoice's totals attributable to this account
			weight = (amount / total_net_for_invoice) if total_net_for_invoice else (1 / len(income_map))

			row = data[income_account]
			row["no_of_invoices"] += 1
			row["net_total"] += amount
			row["tax_total"] += invoice_tax_total * weight
			row["grand_total"] += flt(inv.base_grand_total) * weight
			row["rounded_total"] += flt(inv.base_rounded_total) * weight
			row["outstanding_amount"] += flt(inv.outstanding_amount) * weight
			row["cash"] += flt(inv.paid_amount) * weight

			row["mode_of_payment"].update(mop_set)
			row["sales_order"].update(so_dn_map.get("sales_order", []))
			row["delivery_note"].update(so_dn_map.get("delivery_note", []))

			acct_item_details = item_details_by_account.get((inv.name, income_account), {})
			row["cost_center"].update(acct_item_details.get("cost_center", []))
			row["warehouse"].update(acct_item_details.get("warehouse", []))

	final_data = []
	for income_account, row in data.items():
		row["sales_order"] = ", ".join(sorted(row["sales_order"]))
		row["delivery_note"] = ", ".join(sorted(row["delivery_note"]))
		row["cost_center"] = ", ".join(sorted(row["cost_center"]))
		row["warehouse"] = ", ".join(sorted(row["warehouse"]))
		row["mode_of_payment"] = ", ".join(sorted(row["mode_of_payment"]))
		final_data.append(row)

	final_data.sort(key=lambda r: r["income_account"])

	payment_summary = get_direct_payment_entry_total(filters)
	if payment_summary.get("total_payment"):
		final_data.append(
			{
				"income_account": _("Total Payment Entries Received (Direct)"),
				"no_of_invoices": payment_summary.get("no_of_payments") or 0,
				"net_total": 0,
				"tax_total": 0,
				"grand_total": 0,
				"rounded_total": 0,
				"outstanding_amount": 0,
				"cash": flt(payment_summary.get("total_payment")),
				"sales_order": "",
				"delivery_note": "",
				"cost_center": "",
				"warehouse": "",
				"mode_of_payment": "",
			}
		)

	return columns, final_data


def get_direct_payment_entry_total(filters):
	"""Grand total of Payment Entries received directly from customers
	(payment_type='Receive'), independent of which Sales Invoice/income
	account they were later allocated against. Shown as one extra summary
	row appended at the end of the report."""
	conditions = ""

	if filters.get("from_date"):
		conditions += " and pe.posting_date >= %(from_date)s"
	if filters.get("to_date"):
		conditions += " and pe.posting_date <= %(to_date)s"
	if filters.get("company"):
		conditions += " and pe.company = %(company)s"
	if filters.get("customer"):
		conditions += " and pe.party = %(customer)s"
	if filters.get("owner"):
		conditions += " and pe.owner = %(owner)s"
	if filters.get("mode_of_payment"):
		conditions += " and pe.mode_of_payment = %(mode_of_payment)s"

	result = frappe.db.sql(
		"""
		SELECT
			SUM(pe.paid_amount) AS total_payment,
			COUNT(pe.name) AS no_of_payments
		FROM `tabPayment Entry` pe
		WHERE
			pe.payment_type = 'Receive'
			AND pe.party_type = 'Customer'
			AND pe.docstatus = 1
			{conditions}
		""".format(
			conditions=conditions
		),
		filters,
		as_dict=1,
	)

	return result[0] if result else {"total_payment": 0, "no_of_payments": 0}


def get_columns(additional_table_columns):
	columns = [
		{
			"label": _("Income Account"),
			"fieldname": "income_account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 400,
		},
	]

	if additional_table_columns:
		columns += additional_table_columns

	columns += [
		{
			"label": _("No. of Invoices"),
			"fieldname": "no_of_invoices",
			"fieldtype": "Int",
			"width": 300,
		},
	
		
	
		{
			"label": _("Cash"),
			"fieldname": "cash",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
	
		
	]

	return columns


def get_conditions(filters):
	conditions = ""

	accounting_dimensions = get_accounting_dimensions(as_list=False) or []
	accounting_dimensions_list = [d.fieldname for d in accounting_dimensions]

	if filters.get("company"):
		conditions += " and company=%(company)s"

	if filters.get("customer") and "customer" not in accounting_dimensions_list:
		conditions += " and customer = %(customer)s"

	if filters.get("from_date"):
		conditions += " and posting_date >= %(from_date)s"
	if filters.get("to_date"):
		conditions += " and posting_date <= %(to_date)s"

	if filters.get("owner"):
		conditions += " and owner = %(owner)s"

	def get_sales_invoice_item_field_condition(field, table="Sales Invoice Item") -> str:
		if not filters.get(field) or field in accounting_dimensions_list:
			return ""
		return f""" and exists(select name from `tab{table}`
				where parent=`tabSales Invoice`.name
					and ifnull(`tab{table}`.{field}, '') = %({field})s)"""

	conditions += get_sales_invoice_item_field_condition("mode_of_payment", "Sales Invoice Payment")
	conditions += get_sales_invoice_item_field_condition("cost_center")
	conditions += get_sales_invoice_item_field_condition("warehouse")
	conditions += get_sales_invoice_item_field_condition("brand")
	conditions += get_sales_invoice_item_field_condition("item_group")

	if accounting_dimensions:
		common_condition = """
			and exists(select name from `tabSales Invoice Item`
				where parent=`tabSales Invoice`.name
			"""
		for dimension in accounting_dimensions:
			if filters.get(dimension.fieldname):
				if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
					filters[dimension.fieldname] = get_dimension_with_children(
						dimension.document_type, filters.get(dimension.fieldname)
					)

					conditions += (
						common_condition
						+ "and ifnull(`tabSales Invoice`.{0}, '') in %({0})s)".format(dimension.fieldname)
					)
				else:
					conditions += (
						common_condition
						+ "and ifnull(`tabSales Invoice`.{0}, '') in %({0})s)".format(dimension.fieldname)
					)

	return conditions


def get_invoices(filters, additional_query_columns):
	if additional_query_columns:
		additional_query_columns = ", " + ", ".join(additional_query_columns)

	conditions = get_conditions(filters)
	return frappe.db.sql(
		"""
		select name, posting_date, debit_to, project, customer,
		customer_name, owner, remarks, territory, tax_id, customer_group,
		base_net_total, base_grand_total, base_rounded_total, outstanding_amount, paid_amount,
		is_internal_customer, represents_company, company {0}
		from `tabSales Invoice`
		where docstatus = 1 %s order by posting_date desc, name desc""".format(
			additional_query_columns or ""
		)
		% conditions,
		filters,
		as_dict=1,
	)


def get_income_accounts(invoice_list):
	if not invoice_list:
		return []

	return frappe.db.sql_list(
		"""select distinct income_account
		from `tabSales Invoice Item` where docstatus = 1 and parent in (%s)
		order by income_account"""
		% ", ".join(["%s"] * len(invoice_list)),
		tuple(inv.name for inv in invoice_list),
	)


def get_invoice_income_map(invoice_list):
	income_details = frappe.db.sql(
		"""select parent, income_account, sum(base_net_amount) as amount
		from `tabSales Invoice Item` where parent in (%s) group by parent, income_account"""
		% ", ".join(["%s"] * len(invoice_list)),
		tuple(inv.name for inv in invoice_list),
		as_dict=1,
	)

	invoice_income_map = {}
	for d in income_details:
		invoice_income_map.setdefault(d.parent, frappe._dict())
		invoice_income_map[d.parent][d.income_account] = flt(d.amount)

	return invoice_income_map


def get_invoice_tax_map(invoice_list, invoice_income_map, income_accounts):
	tax_details = frappe.db.sql(
		"""select parent, account_head,
		sum(base_tax_amount_after_discount_amount) as tax_amount
		from `tabSales Taxes and Charges` where parent in (%s) group by parent, account_head"""
		% ", ".join(["%s"] * len(invoice_list)),
		tuple(inv.name for inv in invoice_list),
		as_dict=1,
	)

	invoice_tax_map = {}
	for d in tax_details:
		if d.account_head in income_accounts and d.parent in invoice_income_map:
			if d.account_head in invoice_income_map[d.parent]:
				invoice_income_map[d.parent][d.account_head] += flt(d.tax_amount)
			else:
				invoice_income_map[d.parent][d.account_head] = flt(d.tax_amount)
		else:
			invoice_tax_map.setdefault(d.parent, frappe._dict())
			invoice_tax_map[d.parent][d.account_head] = flt(d.tax_amount)

	return invoice_income_map, invoice_tax_map


def get_invoice_so_dn_map(invoice_list):
	si_items = frappe.db.sql(
		"""select parent, sales_order, delivery_note, so_detail
		from `tabSales Invoice Item` where parent in (%s)
		and (ifnull(sales_order, '') != '' or ifnull(delivery_note, '') != '')"""
		% ", ".join(["%s"] * len(invoice_list)),
		tuple(inv.name for inv in invoice_list),
		as_dict=1,
	)

	invoice_so_dn_map = {}
	for d in si_items:
		if d.sales_order:
			invoice_so_dn_map.setdefault(d.parent, frappe._dict()).setdefault("sales_order", []).append(
				d.sales_order
			)

		delivery_note_list = None
		if d.delivery_note:
			delivery_note_list = [d.delivery_note]
		elif d.sales_order:
			delivery_note_list = frappe.db.sql_list(
				"""select distinct parent from `tabDelivery Note Item`
				where docstatus=1 and so_detail=%s""",
				d.so_detail,
			)

		if delivery_note_list:
			invoice_so_dn_map.setdefault(d.parent, frappe._dict()).setdefault(
				"delivery_note", delivery_note_list
			)

	return invoice_so_dn_map


def get_item_details_by_account(invoice_list):
	"""Cost center / warehouse per invoice, split out by income account."""
	si_items = frappe.db.sql(
		"""select parent, income_account, cost_center, warehouse
		from `tabSales Invoice Item` where parent in (%s)
		and (ifnull(cost_center, '') != '' or ifnull(warehouse, '') != '')"""
		% ", ".join(["%s"] * len(invoice_list)),
		tuple(inv.name for inv in invoice_list),
		as_dict=1,
	)

	item_details = {}
	for d in si_items:
		key = (d.parent, d.income_account)
		if d.cost_center:
			item_details.setdefault(key, {}).setdefault("cost_center", []).append(d.cost_center)
		if d.warehouse:
			item_details.setdefault(key, {}).setdefault("warehouse", []).append(d.warehouse)

	return item_details


def get_mode_of_payments(invoice_list):
	mode_of_payments = {}
	if invoice_list:
		inv_mop = frappe.db.sql(
			"""select parent, mode_of_payment
			from `tabSales Invoice Payment` where parent in (%s) group by parent, mode_of_payment"""
			% ", ".join(["%s"] * len(invoice_list)),
			tuple(invoice_list),
			as_dict=1,
		)

		for d in inv_mop:
			mode_of_payments.setdefault(d.parent, []).append(d.mode_of_payment)

	return mode_of_payments