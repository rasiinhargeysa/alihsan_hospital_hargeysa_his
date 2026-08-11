// Copyright (c) 2026
// For license information, please see license.txt

frappe.query_reports["Forecast form summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			reqd: 0,
		},
		{
			fieldname: "chart_type",
			label: __("Chart View"),
			fieldtype: "Select",
			options: [
				"Detailed",
				"Income vs Expenses",
				"Expense Breakdown",
				"Income Breakdown",
				"Profit Trend",
				"Break-Even Analysis",
				"Target Profit Analysis",
			].join("\n"),
			default: "Detailed",
			reqd: 0,
		},
		{
			fieldname: "investment_amount",
			label: __("Investment Amount (optional, for ROI)"),
			fieldtype: "Currency",
			reqd: 0,
		},
		{
			// What one forecast's profit actually represents. Drives the
			// payback maths — not just the label.
			fieldname: "profit_basis",
			label: __("Forecast Profit Is"),
			fieldtype: "Select",
			options: ["Monthly", "Annual"].join("\n"),
			default: "Monthly",
			reqd: 0,
			depends_on: "eval:doc.chart_type == 'Break-Even Analysis' && doc.investment_amount",
		},
		{
			// Display unit only.
			fieldname: "payback_unit",
			label: __("Show Payback In"),
			fieldtype: "Select",
			options: ["Years", "Months"].join("\n"),
			default: "Years",
			reqd: 0,
			depends_on: "eval:doc.chart_type == 'Break-Even Analysis' && doc.investment_amount",
		},
		{
			fieldname: "target_profit_amount",
			label: __("Target Profit Amount"),
			fieldtype: "Currency",
			reqd: 0,
			depends_on: "eval:doc.chart_type == 'Target Profit Analysis'",
		},
	],

	onload: function (report) {
		report.page.add_inner_button(__("Refresh Chart"), function () {
			report.refresh();
		});
	},

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname == "profit_or_loss" && data && data.profit_or_loss < 0) {
			value = `<span style="color:red;">${value}</span>`;
		}
		if (column.fieldname == "margin" && data) {
			let color = data.margin >= 0 ? "green" : "red";
			value = `<span style="color:${color};">${value}</span>`;
		}
		return value;
	},
};