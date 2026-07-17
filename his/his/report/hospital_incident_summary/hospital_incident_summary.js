// Copyright (c) 2026, HIS Module
// For license information, please see license.txt

frappe.query_reports["Hospital Incident Summary"] = {
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
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "incident_type",
			label: __("Incident Type"),
			fieldtype: "Select",
			options: "\nClinical\nNon-Clinical Medication\nEquipment\nSafety\nOther",
		},
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: "\nLow\nMedium\nHigh\nCritical",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nOpen\nUnder Review Investigating\nAction Required\nClosed",
		},
		{
			fieldname: "action_status",
			label: __("Action Status"),
			fieldtype: "Select",
			options: "\nNot Started\nIn Progress\nCompleted",
		},
		{
			fieldname: "investigator",
			label: __("Investigator"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "assigned_to",
			label: __("Assigned To"),
			fieldtype: "Link",
			options: "User",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "severity") {
			const colors = {
				Critical: "red",
				High: "orange",
				Medium: "yellow",
				Low: "green",
			};
			if (data.severity && colors[data.severity]) {
				value = `<span class="indicator-pill ${colors[data.severity]}">${data.severity}</span>`;
			}
		}

		if (column.fieldname === "status") {
			const colors = {
				Open: "red",
				"Under Review Investigating": "orange",
				"Action Required": "yellow",
				Closed: "green",
			};
			if (data.status && colors[data.status]) {
				value = `<span class="indicator-pill ${colors[data.status]}">${data.status}</span>`;
			}
		}

		if (column.fieldname === "overdue" && data.overdue === __("Yes")) {
			value = `<span class="indicator-pill red">${__("Overdue")}</span>`;
		}

		return value;
	},
};