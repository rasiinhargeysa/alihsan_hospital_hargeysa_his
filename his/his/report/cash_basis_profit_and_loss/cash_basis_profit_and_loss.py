# Copyright (c) 2026, Custom Report
# Cash Basis Profit and Loss — built on GL Entry, attributing cash movements
# on Bank/Cash accounts back to the Income/Expense accounts of whatever they
# settled (invoice or journal entry), bucketed by the date cash actually moved.

import frappe
from frappe import _
from frappe.utils import flt, getdate
from collections import defaultdict
from dateutil.relativedelta import relativedelta


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	period_list = get_period_list(filters.from_date, filters.to_date)
	cash_accounts = get_cash_bank_accounts(filters.company)

	if not cash_accounts:
		frappe.msgprint(_("No Cash or Bank accounts found for this company."))
		return [], []

	cash_gl_entries = get_cash_gl_entries(filters, cash_accounts)
	if not cash_gl_entries:
		return get_columns(period_list), []

	voucher_keys = list({(d.voucher_type, d.voucher_no) for d in cash_gl_entries})
	sibling_legs = get_sibling_legs(voucher_keys, cash_accounts)

	invoice_refs = set()
	for leg in sibling_legs:
		if leg.against_voucher_type in ("Sales Invoice", "Purchase Invoice") and leg.against_voucher:
			invoice_refs.add((leg.against_voucher_type, leg.against_voucher))

	invoice_account_map = get_invoice_account_breakup(invoice_refs)

	# category_totals[account][period_key] = amount
	category_totals = defaultdict(lambda: defaultdict(float))
	account_root_type = get_account_root_types()

	siblings_by_voucher = defaultdict(list)
	for leg in sibling_legs:
		siblings_by_voucher[(leg.voucher_type, leg.voucher_no)].append(leg)

	for cash_entry in cash_gl_entries:
		key = (cash_entry.voucher_type, cash_entry.voucher_no)
		siblings = siblings_by_voucher.get(key, [])
		# cash inflow is debit to cash account, outflow is credit
		cash_amount = flt(cash_entry.debit) - flt(cash_entry.credit)
		if not cash_amount:
			continue

		period_key = get_period_key(cash_entry.posting_date, period_list)
		if not period_key:
			continue

		distribute_cash_amount(
			cash_amount,
			siblings,
			invoice_account_map,
			account_root_type,
			category_totals,
			period_key,
		)

	columns = get_columns(period_list)
	data = build_report_rows(category_totals, period_list, account_root_type)
	return columns, data


def validate_filters(filters):
	if not filters.company:
		frappe.throw(_("Company is mandatory"))
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def get_period_list(from_date, to_date):
	"""Builds a list of monthly periods between from_date and to_date."""
	periods = []
	current = getdate(from_date).replace(day=1)
	end = getdate(to_date)

	while current <= end:
		period_end = current + relativedelta(day=31)
		periods.append(
			frappe._dict(
				key=current.strftime("%Y-%m"),
				label=current.strftime("%b %Y"),
				start=current,
				end=min(period_end, end),
			)
		)
		current = current + relativedelta(months=1)

	return periods


def get_period_key(posting_date, period_list):
	posting_date = getdate(posting_date)
	for period in period_list:
		if period.start <= posting_date <= period.end:
			return period.key
	return None


def get_cash_bank_accounts(company):
	return frappe.get_all(
		"Account",
		filters={"company": company, "account_type": ["in", ["Cash", "Bank"]], "is_group": 0},
		pluck="name",
	)


def get_cash_gl_entries(filters, cash_accounts):
	return frappe.get_all(
		"GL Entry",
		filters={
			"company": filters.company,
			"account": ["in", cash_accounts],
			"posting_date": ["between", [filters.from_date, filters.to_date]],
			"is_cancelled": 0,
		},
		fields=["name", "voucher_type", "voucher_no", "posting_date", "debit", "credit", "account"],
	)


def get_sibling_legs(voucher_keys, cash_accounts):
	"""Fetch all non-cash GL Entry legs for the given (voucher_type, voucher_no) pairs."""
	if not voucher_keys:
		return []

	voucher_types = list({v[0] for v in voucher_keys})
	voucher_nos = list({v[1] for v in voucher_keys})

	all_legs = frappe.get_all(
		"GL Entry",
		filters={
			"voucher_type": ["in", voucher_types],
			"voucher_no": ["in", voucher_nos],
			"is_cancelled": 0,
		},
		fields=[
			"name",
			"voucher_type",
			"voucher_no",
			"account",
			"debit",
			"credit",
			"against_voucher_type",
			"against_voucher",
		],
	)

	voucher_key_set = set(voucher_keys)
	cash_account_set = set(cash_accounts)

	return [
		leg
		for leg in all_legs
		if (leg.voucher_type, leg.voucher_no) in voucher_key_set and leg.account not in cash_account_set
	]


def get_invoice_account_breakup(invoice_refs):
	"""
	For each (doctype, name) invoice reference, returns the breakup of its
	non-Receivable/Payable, non-tax GL Entry amounts by account — i.e. its
	income or expense accounts and how much of the invoice total each
	represents. Tax/duty legs are excluded since they're liabilities, not
	income or expense.
	Returns: {(doctype, name): [GL Entry-like dicts with account/debit/credit]}
	"""
	result = {}
	if not invoice_refs:
		return result

	tax_account_types = {"Tax", "Chargeable", "Deemed Positive"}
	account_types = {a.name: a.account_type for a in frappe.get_all("Account", fields=["name", "account_type"])}

	by_doctype = defaultdict(list)
	for doctype, name in invoice_refs:
		by_doctype[doctype].append(name)

	for doctype, names in by_doctype.items():
		legs = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": doctype, "voucher_no": ["in", names], "is_cancelled": 0},
			fields=["voucher_no", "account", "debit", "credit", "party_type"],
		)
		grouped = defaultdict(list)
		for leg in legs:
			# Receivable/Payable legs are identified by having a party (customer/supplier)
			# on the entry — these are the accrual-side legs we want to exclude.
			if leg.party_type:
				continue
			# Tax/duty legs are liabilities, not income or expense — exclude them too.
			if account_types.get(leg.account) in tax_account_types:
				continue
			grouped[leg.voucher_no].append(leg)

		for name in names:
			result[(doctype, name)] = grouped.get(name, [])

	return result


def get_account_root_types():
	accounts = frappe.get_all("Account", fields=["name", "root_type"])
	return {a.name: a.root_type for a in accounts}


def distribute_cash_amount(
	cash_amount, siblings, invoice_account_map, account_root_type, category_totals, period_key
):
	"""
	Distributes a single cash GL movement across the income/expense accounts
	implied by its sibling (non-cash) legs on the same voucher, attributing
	proportionally to each sibling's share of the voucher's non-cash total.
	"""
	if not siblings:
		category_totals[_("Unallocated / Advances")][period_key] += cash_amount
		return

	# total magnitude of non-cash legs on this voucher, used to compute each
	# sibling's proportional share of the cash movement.
	total_sibling_amount = sum(abs(flt(s.debit) - flt(s.credit)) for s in siblings)
	if not total_sibling_amount:
		category_totals[_("Unallocated / Advances")][period_key] += cash_amount
		return

	for sibling in siblings:
		sibling_amount = abs(flt(sibling.debit) - flt(sibling.credit))
		if not sibling_amount:
			continue
		share = sibling_amount / total_sibling_amount
		portion = cash_amount * share

		ref_key = (sibling.against_voucher_type, sibling.against_voucher)
		if sibling.against_voucher_type in ("Sales Invoice", "Purchase Invoice") and ref_key in invoice_account_map:
			invoice_legs = invoice_account_map[ref_key]
			invoice_total = sum(abs(flt(l.debit) - flt(l.credit)) for l in invoice_legs)
			if invoice_total:
				for inv_leg in invoice_legs:
					inv_leg_amount = abs(flt(inv_leg.debit) - flt(inv_leg.credit))
					inv_share = inv_leg_amount / invoice_total
					category_totals[inv_leg.account][period_key] += portion * inv_share
			else:
				category_totals[_("Unallocated / Advances")][period_key] += portion
		else:
			# sibling leg's own account is the category (e.g. direct Journal Entry,
			# or a cash sale posting straight to an Income account)
			root_type = account_root_type.get(sibling.account)
			if root_type in ("Income", "Expense"):
				category_totals[sibling.account][period_key] += portion
			else:
				category_totals[_("Unallocated / Advances")][period_key] += portion


def get_columns(period_list):
	columns = [
		{
			"label": _("Account"),
			"fieldname": "account",
			"fieldtype": "Data",
			"width": 260,
		}
	]
	for period in period_list:
		columns.append(
			{
				"label": period.label,
				"fieldname": period.key,
				"fieldtype": "Currency",
				"width": 130,
			}
		)
	columns.append({"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "width": 140})
	return columns


def build_report_rows(category_totals, period_list, account_root_type):
	income_rows = []
	expense_rows = []
	other_rows = []

	for account, period_amounts in category_totals.items():
		root_type = account_root_type.get(account)
		row = {"account": account}
		total = 0
		for period in period_list:
			amount = flt(period_amounts.get(period.key, 0))
			row[period.key] = amount
			total += amount
		row["total"] = total

		if root_type == "Income":
			income_rows.append(row)
		elif root_type == "Expense":
			expense_rows.append(row)
		else:
			other_rows.append(row)

	income_rows.sort(key=lambda r: r["account"])
	expense_rows.sort(key=lambda r: r["account"])
	other_rows.sort(key=lambda r: r["account"])

	data = []

	data.append({"account": _("Income"), "bold": 1})
	data.extend(income_rows)
	income_total = make_total_row(_("Total Income (Cash Basis)"), income_rows, period_list)
	data.append(income_total)

	data.append({"account": ""})
	data.append({"account": _("Expense"), "bold": 1})
	data.extend(expense_rows)
	expense_total = make_total_row(_("Total Expense (Cash Basis)"), expense_rows, period_list)
	data.append(expense_total)

	if other_rows:
		data.append({"account": ""})
		data.append({"account": _("Other / Unallocated"), "bold": 1})
		data.extend(other_rows)

	data.append({"account": ""})
	net_row = {"account": _("Net Cash Profit / (Loss)"), "bold": 1}
	for period in period_list:
		net_row[period.key] = flt(income_total.get(period.key, 0)) - flt(expense_total.get(period.key, 0))
	net_row["total"] = flt(income_total.get("total", 0)) - flt(expense_total.get("total", 0))
	data.append(net_row)

	return data


def make_total_row(label, rows, period_list):
	total_row = {"account": label, "bold": 1}
	for period in period_list:
		total_row[period.key] = sum(flt(r.get(period.key, 0)) for r in rows)
	total_row["total"] = sum(flt(r.get("total", 0)) for r in rows)
	return total_row