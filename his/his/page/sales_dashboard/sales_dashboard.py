# Add this to your custom app's api.py or create api.py in your app root
# Path: your_app/your_app/api.py

import frappe


@frappe.whitelist()
def get_dashboard_data(from_date, to_date, customer=None, owner=None, cost_center=None):
	"""
	Sales & Payments Dashboard API
	Returns all dashboard data in one call with ignore_permissions=True
	"""
	invoices = frappe.get_all(
		"Sales Invoice",
		filters=[
			["posting_date", ">=", from_date],
			["posting_date", "<=", to_date],
			["docstatus", "=", 1],
		]
		+ ([["customer", "=", customer]] if customer else [])
		+ ([["owner", "=", owner]] if owner else [])
		+ ([["cost_center", "=", cost_center]] if cost_center else []),
		fields=[
			"name",
			"customer",
			"customer_group",
			"posting_date",
			"grand_total",
			"paid_amount",
		],
		ignore_permissions=True,
		limit=0,
	)

	# ── KPIs ──────────────────────────────────────────────────────────────────
	total_sales = 0
	total_paid = 0
	total_unpaid = 0
	total_partial = 0
	unique_customers = set()

	for inv in invoices:
		gt = inv.grand_total or 0
		paid = inv.paid_amount or 0
		unpaid = gt - paid
		total_sales += gt
		unique_customers.add(inv.customer)
		if unpaid <= 0:
			total_paid += gt
		elif paid <= 0:
			total_unpaid += gt
		else:
			total_partial += gt
			total_paid += paid
			total_unpaid += unpaid

	# ── Payment Received ───────────────────────────────────────────────────────
	payment_entries = frappe.get_all(
		"Payment Entry",
		filters=[
			["posting_date", ">=", from_date],
			["posting_date", "<=", to_date],
			["payment_type", "=", "Receive"],
			["docstatus", "=", 1],
		],
		fields=["paid_amount"],
		ignore_permissions=True,
		limit=0,
	)
	
	payment_received = sum([pe.paid_amount or 0 for pe in payment_entries])

	# ── Top Customers ─────────────────────────────────────────────────────────
	customer_map = {}
	for inv in invoices:
		c = inv.customer
		if c not in customer_map:
			customer_map[c] = {"total": 0, "paid": 0, "unpaid": 0}
		gt = inv.grand_total or 0
		paid = inv.paid_amount or 0
		unpaid = gt - paid
		customer_map[c]["total"] += gt
		customer_map[c]["paid"] += paid
		customer_map[c]["unpaid"] += unpaid

	top_customers = sorted(
		[{"customer": k, **v} for k, v in customer_map.items()],
		key=lambda x: x["total"],
		reverse=True,
	)

	# ── Income by Customer Group ───────────────────────────────────────────────
	cg_map = {}
	for inv in invoices:
		g = inv.customer_group or "Other"
		cg_map[g] = (cg_map.get(g) or 0) + (inv.grand_total or 0)

	by_customer_group = [{"group": k, "amount": v} for k, v in cg_map.items()]
	by_customer_group.sort(key=lambda x: x["amount"], reverse=True)

	# ── Income by Date ─────────────────────────────────────────────────────────
	date_map = {}
	for inv in invoices:
		d = str(inv.posting_date)
		if d not in date_map:
			date_map[d] = {"sales": 0, "paid": 0, "outstanding": 0}
		gt = inv.grand_total or 0
		paid = inv.paid_amount or 0
		outstanding = gt - paid
		date_map[d]["sales"] += gt
		date_map[d]["paid"] += paid
		date_map[d]["outstanding"] += outstanding

	by_date = [{"date": k, **v} for k, v in sorted(date_map.items())]

	# ── Income by Item Group ───────────────────────────────────────────────────
	invoice_names = [inv.name for inv in invoices]
	by_item_group = []

	if invoice_names:
		ig_filters = [
			["parent", "in", invoice_names],
		]

		items = frappe.get_all(
			"Sales Invoice Item",
			filters=ig_filters,
			fields=["item_group", "net_amount", "qty"],
			ignore_permissions=True,
			limit=0,
		)

		ig_map = {}
		for row in items:
			g = row.item_group or "Other"
			if g not in ig_map:
				ig_map[g] = {"amount": 0, "qty": 0}
			ig_map[g]["amount"] += row.net_amount or 0
			ig_map[g]["qty"] += row.qty or 0

		by_item_group = [{"group": k, **v} for k, v in ig_map.items()]
		by_item_group.sort(key=lambda x: x["amount"], reverse=True)

	return {
		"kpis": {
			"total_sales": total_sales,
			"total_paid": total_paid,
			"total_unpaid": total_unpaid,
			"total_partial": total_partial,
			"total_orders": len(invoices),
			"total_customers": len(unique_customers),
			"payment_received": payment_received,
		},
		"top_customers": top_customers[:10],
		"customer_table": top_customers,
		"by_customer_group": by_customer_group,
		"by_item_group": by_item_group,
		"by_date": by_date,
	}


@frappe.whitelist()
def get_filter_options():
	"""Return dropdown options for filters."""
	customers = frappe.get_all(
		"Customer", fields=["name"], order_by="name", ignore_permissions=True, limit=0
	)
	users = frappe.get_all(
		"User", fields=["name"], order_by="name", ignore_permissions=True, limit=0
	)
	cost_centers = frappe.get_all(
		"Cost Center", fields=["name"], order_by="name", ignore_permissions=True, limit=0
	)

	return {
		"customers": [c.name for c in customers],
		"users": [u.name for u in users],
		"cost_centers": [cc.name for cc in cost_centers],
	}
