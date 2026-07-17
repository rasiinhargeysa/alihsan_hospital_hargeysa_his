# Copyright (c) 2022, Anfac Tech and contributors
# For license information, please see license.txt

import frappe

def execute(filters=None):
	
	return get_columns(), get_data(filters)

def get_data(filters):
	
	_from ,to  = filters.get('from_date'), filters.get('to') 
	
	data = frappe.db.sql(f"""
	select 
	posting_date,
	name, 
	customer ,
	outstanding_amount  

from `tabSales Invoice`
where posting_date between "{_from}" and "{to}" and outstanding_amount > 0 and status != "Cancelled" and status != "Draft"
 ;""")
	return data
def get_columns():
	return [

		"Date: Date:120",
		"Voucher:Link/Sales Invoice:220",
		"Customer Name:Link/Customer:200", 
		"Outstanding Amount:Currency:110",
	
		
	]

