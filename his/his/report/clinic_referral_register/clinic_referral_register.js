// Copyright (c) 2026, HIS
// Report: Clinic Referral Register
// Path  : his/his/report/clinic_referral_register/clinic_referral_register.js

frappe.query_reports["Clinic Referral Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "referred__clinic",
			label: __("Referred Clinic"),
			fieldtype: "Select",
			options: [
				"",
				"Al Ihsan Eye Clinic",
				"Al Ihsan Medical Center (Siinay)",
				"Al Ihsan Medical Center (Ina Mooge)",
				"Al Ihsan Medical Center (Kilelka)",
			],
		},
		{
			fieldname: "priority",
			label: __("Priority"),
			fieldtype: "Select",
			options: ["", "Elective", "Urgent", "Emergency"],
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Data",
		},
		{
			fieldname: "requested_speciality",
			label: __("Requested Speciality"),
			fieldtype: "Data",
		},
		{
			fieldname: "sex",
			label: __("Sex"),
			fieldtype: "Link",
			options: "Gender",
		},
		{
			fieldname: "patient",
			label: __("Patient Name or PID"),
			fieldtype: "Data",
		},
		{
			fieldname: "user",
			label: __("Referred By"),
			fieldtype: "Link",
			options: "User",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "priority" && data && data.priority) {
			const tone = {
				Emergency: "#a41f24",
				Urgent: "#a5680d",
				Elective: "#1f6b57",
			}[data.priority];

			if (tone) {
				value = `<span style="color:${tone};font-weight:600">${data.priority}</span>`;
			}
		}

		if (column.fieldname === "net_amount") {
			value = `<b>${value}</b>`;
		}

		return value;
	},
};