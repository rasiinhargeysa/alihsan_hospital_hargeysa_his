# Copyright (c) 2015, Frappe Technologies Pvt. Ltd.
# For license information, please see license.txt

import frappe
from frappe import _, scrub
from frappe.utils import cint, flt
from six import iteritems

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


def execute(filters=None):
	args = {
		"party_type": "Customer",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}
	return AccountsReceivableSummary(filters).run(args)


class AccountsReceivableSummary(ReceivablePayableReport):
	def run(self, args):
		self.party_type = args.get("party_type")
		self.party_naming_by = frappe.db.get_value(
			args.get("naming_by")[0], None, args.get("naming_by")[1]
		)
		self.get_columns()
		self.get_data(args)
		return self.columns, self.data

	def get_data(self, args):
		self.data = []

		# Base report
		self.receivables = ReceivablePayableReport(self.filters).run(args)[1]
		self.get_party_total(args)

		parties = list(self.party_total.keys())

		# ✅ Batch fetch everything from Customer (ONLY parties in report)
		customer_fields = ["name", "custom_patient", "custom_mobile", "responsible"]
		if self.party_naming_by == "Naming Series":
			customer_fields.append("customer_name")

		customer_map = frappe._dict({
			c.name: c
			for c in frappe.get_all(
				"Customer",
				filters={"name": ["in", parties]},
				fields=customer_fields
			)
		})

		# ✅ GL balance
		gl_balance_map = {}
		if self.filters.show_gl_balance:
			gl_balance_map = get_gl_balance(self.filters.report_date)

		for party, party_dict in iteritems(self.party_total):
			if round(party_dict.outstanding, 10) == 0:
				continue

			cust = customer_map.get(party, frappe._dict())

			row = frappe._dict()
			row.party = party

			if self.party_naming_by == "Naming Series":
				row.party_name = cust.get("customer_name")

			# ✅ Read from Customer fields
			row.patient = cust.get("custom_patient")
			row.mobile_no = cust.get("custom_mobile")
			row.responsible = cust.get("responsible")
			row.responsible_number = cust.get("responsible_number")

			# Inline buttons
			row.receipt = (
				f"""<button style='padding: 3px; margin:-5px' class='btn btn-primary'
					onClick='receipt("{party}" , "{party_dict.outstanding}" , "{party_dict.outstanding * 1.05}")'>
					Receipt</button>"""
			)

			row.statement = (
				f"""<button style='padding: 3px; margin:-5px' class='btn btn-primary'
					onClick='statement("{party}")'>Statements</button>"""
			)

			row.update(party_dict)

			if self.filters.show_gl_balance:
				row.gl_balance = gl_balance_map.get(party)
				row.diff = flt(row.outstanding) - flt(row.gl_balance)

			if self.filters.show_future_payments:
				row.remaining_balance = flt(row.outstanding) - flt(row.future_amount)

			self.data.append(row)

	def get_party_total(self, args):
		self.party_total = frappe._dict()

		for d in self.receivables:
			self.init_party_total(d)

			# Add all amount columns
			for k in list(self.party_total[d.party]):
				if k not in ["currency", "sales_person"]:
					self.party_total[d.party][k] += d.get(k, 0.0)

			# set territory, customer_group, sales person etc
			self.set_party_details(d)

	def init_party_total(self, row):
		self.party_total.setdefault(
			row.party,
			frappe._dict(
				{
					"invoiced": 0.0,
					"paid": 0.0,
					"credit_note": 0.0,
					"outstanding": 0.0,
				}
			),
		)

	def set_party_details(self, row):
		self.party_total[row.party].currency = row.currency

		for key in ("territory", "customer_group", "supplier_group"):
			if row.get(key):
				self.party_total[row.party][key] = row.get(key)

		# NOTE: base report sometimes provides sales_person as string, so keep safe
		if row.sales_person:
			if not self.party_total[row.party].get("sales_person"):
				self.party_total[row.party].sales_person = []
			self.party_total[row.party].sales_person.append(row.sales_person)

		if self.filters.sales_partner:
			self.party_total[row.party]["default_sales_partner"] = row.get("default_sales_partner")

	def get_columns(self):
		self.columns = []

		self.add_column(
			label=_(self.party_type),
			fieldname="party",
			fieldtype="Link",
			options=self.party_type,
			width=180,
		)

		if self.party_naming_by == "Naming Series":
			self.add_column(
				_("{0} Name").format(self.party_type),
				fieldname="party_name",
				fieldtype="Data",
				width=300,
			)

		self.add_column(_("Patient"), fieldname="patient", fieldtype="Data")
		self.add_column(_("Mobile No"), fieldname="mobile_no", fieldtype="Data")
		self.add_column(_("Responsible"), fieldname="responsible", fieldtype="Link", options="Responsible")

		self.add_column(_("Balance"), fieldname="outstanding")
		self.add_column(_("Receipt"), fieldname="receipt", fieldtype="Data")
		self.add_column(_("Print Statement"), fieldname="statement", fieldtype="Data")

		if self.filters.show_gl_balance:
			self.add_column(_("GL Balance"), fieldname="gl_balance")
			self.add_column(_("Difference"), fieldname="diff")

		if self.filters.show_future_payments:
			self.add_column(label=_("Future Payment Amount"), fieldname="future_amount")
			self.add_column(label=_("Remaining Balance"), fieldname="remaining_balance")

	def setup_ageing_columns(self):
		for i, label in enumerate(
			[
				"0-{range1}".format(range1=self.filters["range1"]),
				"{range1}-{range2}".format(
					range1=cint(self.filters["range1"]) + 1, range2=self.filters["range2"]
				),
				"{range2}-{range3}".format(
					range2=cint(self.filters["range2"]) + 1, range3=self.filters["range3"]
				),
				"{range3}-{range4}".format(
					range3=cint(self.filters["range3"]) + 1, range4=self.filters["range4"]
				),
				"{range4}-{above}".format(range4=cint(self.filters["range4"]) + 1, above=_("Above")),
			]
		):
			self.add_column(label=label, fieldname="range" + str(i + 1))

		self.add_column(label="Total Amount Due", fieldname="total_due")


def get_gl_balance(report_date):
	return frappe._dict(
		frappe.db.get_all(
			"GL Entry",
			fields=["party", "sum(debit - credit)"],
			filters={"posting_date": ("<=", report_date), "is_cancelled": 0},
			group_by="party",
			as_list=1,
		)
	)
