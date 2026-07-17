/* Vision Screening Report — Client-side JS
 * Defines filters and applies conditional row formatting.
 */

frappe.query_reports["Vision Screening Report"] = {

	// ─── Filters ──────────────────────────────────────────────────────────────

	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 0,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 0,
		},
		{
			fieldname: "doctor",
			label: __("Doctor"),
			fieldtype: "Link",
			options: "Healthcare Practitioner",
		},
		{
			fieldname: "sex",
			label: __("Sex"),
			fieldtype: "Link",
			options: "Gender",
		},
		{
			fieldname: "overall_vision_status",
			label: __("Vision Status"),
			fieldtype: "Select",
			options: "\nNormal\nAbnormal",
		},
		{
			fieldname: "diagnosis",
			label: __("Diagnosis"),
			fieldtype: "Link",
			options: "Diagnosis",
		},
	],

	// ─── Row formatter ────────────────────────────────────────────────────────
	// Highlights rows based on vision status for quick triage.

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (!data) return value;

		// Highlight full row for abnormal vision
		if (data.overall_vision_status === "Abnormal") {
			value = `<span style="color: #c0392b; font-weight: 500;">${value}</span>`;
		}

		// Vision Status badge
		if (column.fieldname === "overall_vision_status") {
			if (data.overall_vision_status === "Normal") {
				value = `<span class="indicator-pill green"
					style="padding: 2px 8px; border-radius: 10px;
					       background:#d4edda; color:#155724; font-size:11px;">
					${__("Normal")}
				</span>`;
			} else if (data.overall_vision_status === "Abnormal") {
				value = `<span class="indicator-pill red"
					style="padding: 2px 8px; border-radius: 10px;
					       background:#f8d7da; color:#721c24; font-size:11px;">
					${__("Abnormal")}
				</span>`;
			}
		}

		// Yes/No pill badges for key boolean fields
		const yesNoFields = [
			"history_of_eye_pain_and_discomfort",
			"difficult_in_vision_near_far",
			"history_of_redness_tching_in_eyes",
			"history_of_watering_or_discharge",
			"do_you_wear_glasses_or_contact_lenses",
			"history_of_chronic_diseases_dmhtn",
		];

		if (yesNoFields.includes(column.fieldname)) {
			const raw = data[column.fieldname];
			if (raw === "Yes") {
				value = `<span style="padding:2px 8px; border-radius:10px;
					background:#fff3cd; color:#856404; font-size:11px;">
					${__("Yes")}
				</span>`;
			} else if (raw === "No") {
				value = `<span style="padding:2px 8px; border-radius:10px;
					background:#e2e3e5; color:#383d41; font-size:11px;">
					${__("No")}
				</span>`;
			}
		}

		return value;
	},

	// ─── After Render ─────────────────────────────────────────────────────────
	// Adds a legend banner below the toolbar.

	// onload(report) {
	// 	report.page.add_inner_button(__("Print Report"), () => {
	// 		frappe.query_report.print_report();
	// 	});
	// },

	// ─── Tree / Group By ─────────────────────────────────────────────────────
	// Allow grouping by doctor in the report builder.

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
			events: {
				onCheckRow(data) {
					if (!data || !data.length) return;
				},
			},
		});
	},
};