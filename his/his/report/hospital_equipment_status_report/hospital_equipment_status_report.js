// Copyright (c) 2026, Your Organization and contributors
// For license information, please see license.txt

frappe.query_reports["Hospital Equipment Status Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "user",
			label: __("Submitted By"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"Not Requested",
				"Requested",
				"Pending Delivery",
				"Delivered",
				"In Progress",
				"Other",
			],
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "equipment",
			label: __("Equipment"),
			fieldtype: "Data",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "status" && data && data.status) {
			let color_map = {
				"Not Requested": "grey",
				Requested: "orange",
				"Pending Delivery": "yellow",
				Delivered: "green",
				"In Progress": "blue",
				Other: "grey",
			};
			let color = color_map[data.status] || "grey";
			value = `<span class="indicator-pill ${color}">${data.status}</span>`;
		}

		return value;
	},

	onload: function (report) {
		report.page.add_inner_button(__("Refresh"), function () {
			report.refresh();
		});
	},
};