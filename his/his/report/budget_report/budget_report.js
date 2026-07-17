// Copyright (c) 2026, Rasiin Tech and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Budget Report"] = {
	tree: true,
	name_field: "particulars",
	parent_field: "parent_particulars",
	initial_depth: 1,

	filters: [
		{
			fieldname: "from_fiscal_year",
			label: __("From Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: frappe.sys_defaults.fiscal_year,
			reqd: 1
		},
		{
			fieldname: "to_fiscal_year",
			label: __("To Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: frappe.sys_defaults.fiscal_year,
			reqd: 1
		},
		{
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: [
				{ value: "Monthly", label: __("Monthly") },
				{ value: "Quarterly", label: __("Quarterly") },
				{ value: "Half-Yearly", label: __("Half-Yearly") },
				{ value: "Yearly", label: __("Yearly") }
			],
			default: "Yearly",
			reqd: 1
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1
		},
		{
			fieldname: "budget_against",
			label: __("Budget Against"),
			fieldtype: "Select",
			options: ["Cost Center", "Project"],
			default: "Cost Center",
			reqd: 1,
			on_change: function(report) {
				report.set_filter_value("budget_against_filter", []);
				report.refresh();
			}
		},
		{
			fieldname: "budget_against_filter",
			label: __("Dimension Filter"),
			fieldtype: "MultiSelectList",
			get_data: function(txt) {
				let budget_against = frappe.query_report.get_filter_value("budget_against");
				if (!budget_against) return [];
				return frappe.db.get_link_options(budget_against, txt);
			}
		},
		{
			fieldname: "show_cumulative",
			label: __("Show Cumulative Amount"),
			fieldtype: "Check",
			default: 0
		},
		// Hidden — set programmatically when polling a background job
		{
			fieldname: "job_id",
			label: __("Job ID"),
			fieldtype: "Data",
			hidden: 1
		}
	],

	onload: function(report) {
		if (!report.get_filter_value("period")) {
			report.set_filter_value("period", "Yearly");
		}
		if (!report.get_filter_value("budget_against")) {
			report.set_filter_value("budget_against", "Cost Center");
		}

		// Store reference for use in after_datatable_render
		this._report = report;
	},

	// -----------------------------------------------------------------------
	// after_datatable_render fires each time the report table is drawn.
	// We inspect the first data row: if it contains our sentinel job_id
	// string, start polling.
	// -----------------------------------------------------------------------
	after_datatable_render: function(datatable) {
		const report = frappe.query_report;
		const data   = report.data || [];
		if (!data.length) return;

		const firstRow = data[0];
		const status   = firstRow.status || "";

		const match = status.match(/Job ID:\s*(\S+)/);
		if (!match) return;

		const job_id = match[1];
		this._startPolling(report, job_id);
	},

	_startPolling: function(report, job_id) {
		// Avoid starting multiple pollers
		if (this._poller) return;

		frappe.show_alert({ message: __("Report queued. Polling for results…"), indicator: "blue" });

		this._poller = setInterval(() => {
			// Inject the job_id into filters and refresh silently
			report.set_filter_value("job_id", job_id);

			frappe.call({
				method: "frappe.desk.query_report.run",
				args: {
					report_name: report.report_name,
					filters: report.get_values(),
				},
				callback: (r) => {
					if (!r.message) return;

					const { result } = r.message;
					if (!result || !result.length) return;

					const firstStatus = (result[0].status || "");

					if (firstStatus.includes("Still processing")) {
						// Still pending — keep polling
						return;
					}

					// Done (or error) — stop polling and re-render
					clearInterval(this._poller);
					this._poller = null;

					// Clear the job_id so the next manual refresh runs fresh
					report.set_filter_value("job_id", "");

					frappe.show_alert({ message: __("Budget report ready!"), indicator: "green" });
					report.render_report(r.message);
				}
			});
		}, 4000); // poll every 4 seconds
	},

	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (!data) return value;

		const bold_rows = [
			"Income",
			"Total Income",
			"Expense",
			"Total Expense",
			"Net Profit / Loss"
		];

		if (bold_rows.includes(data.particulars)) {
			value = `<b>${value}</b>`;
		}

		if (data.is_total) {
			value = `<b style="color:#1f4e79;">${value}</b>`;
		}

		if (data.is_net) {
			value = `<b style="color:#1a7f37;">${value}</b>`;
		}

		if (
			column.fieldname &&
			column.fieldname.indexOf("variance") !== -1 &&
			typeof data[column.fieldname] === "number" &&
			data[column.fieldname] < 0
		) {
			value = `<span style="color:black;font-weight:600;">${value}</span>`;
		}

		return value;
	}
};

if (window.erpnext && erpnext.dimension_filters) {
	erpnext.dimension_filters.forEach((dimension) => {
		if (!frappe.query_reports["Budget Report"].filters[4].options.includes(dimension.document_type)) {
			frappe.query_reports["Budget Report"].filters[4].options.push(dimension.document_type);
		}
	});
}