// Copyright (c) 2026, Al-Ihsan Hospital and contributors
// For license information, please see license.txt

/* eslint-disable */

frappe.query_reports["Queue Time Management"] = {
    filters: [
        {
            fieldname: "date",
            label: __("Date"),
            fieldtype: "Date",
            default: frappe.datetime.now_date(),
            reqd: 1,
        },
        {
            fieldname: "practitioner",
            label: __("Doctor"),
            fieldtype: "Link",
            options: "Healthcare Practitioner",
        },
        {
            fieldname: "department",
            label: __("Department"),
            fieldtype: "Link",
            options: "Medical Department",
        },
        {
            fieldname: "patient",
            label: __("Patient"),
            fieldtype: "Link",
            options: "Patient",
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        if ([
            "queue_to_encounter",
            "encounter_to_collection",
            "collection_to_result",
        ].includes(column.fieldname)) {
            if (String(data[column.fieldname] || "").toLowerCase() === "yet") {
                return `<span style="color:var(--text-muted);font-style:italic">${value}</span>`;
            }
        }

        if (column.fieldname === "queue_to_encounter") {
            const minutes = Number(data.queue_to_encounter_minutes);
            if (Number.isFinite(minutes) && minutes > 30) {
                return `<strong style="color:#dc2626">${value}</strong>`;
            }
            if (Number.isFinite(minutes) && minutes > 20) {
                return `<strong style="color:#d97706">${value}</strong>`;
            }
        }

        if (column.fieldname === "queue_status") {
            const status = String(data.queue_status_code || "GREY").toLowerCase();
            const map = {
                green: ["#dcfce7", "#166534"],
                blue: ["#dbeafe", "#1d4ed8"],
                amber: ["#fef3c7", "#92400e"],
                red: ["#fee2e2", "#991b1b"],
                grey: ["#e2e8f0", "#475569"],
            };
            const [background, color] = map[status] || map.grey;
            return `<span style="display:inline-flex;padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;background:${background};color:${color}">${value}</span>`;
        }

        return value;
    },

    onload(report) {
        report.page.add_inner_button(__("Today"), () => {
            report.set_filter_value("date", frappe.datetime.now_date());
            report.refresh();
        }, __("Period"));

        report.page.add_inner_button(__("Yesterday"), () => {
            report.set_filter_value(
                "date",
                frappe.datetime.add_days(frappe.datetime.now_date(), -1)
            );
            report.refresh();
        }, __("Period"));
    },
};
