frappe.query_reports["Doctor Sales"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "width": "80"
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today()
        },
        {
            "fieldname": "customer",
            "label": __("Customer"),
            "fieldtype": "Link",
            "options": "Customer"
        },
        {
            "fieldname": "company",
            "label": __("Company"),
            "fieldtype": "Link",
            "options": "Company",
            "default": frappe.defaults.get_user_default("Company")
        },
        {
            "fieldname": "mode_of_payment",
            "label": __("Mode of Payment"),
            "fieldtype": "Link",
            "options": "Mode of Payment"
        },
        {
            "fieldname": "owner",
            "label": __("Owner"),
            "fieldtype": "Link",
            "options": "User",
            "hidden": 1
        },
        {
            "fieldname": "cost_center",
            "label": __("Cost Center"),
            "fieldtype": "Link",
            "options": "Cost Center"
        },
        {
            "fieldname": "warehouse",
            "label": __("Warehouse"),
            "fieldtype": "Link",
            "options": "Warehouse"
        },
        {
            "fieldname": "brand",
            "label": __("Brand"),
            "fieldtype": "Link",
            "options": "Brand"
        },
        {
            "fieldname": "item_group",
            "label": __("Item Group"),
            "fieldtype": "Link",
            "options": "Item Group"
        }
    ],

    get_chart_data: function(columns, result) {
        if (!result || !result.length) {
            return null;
        }

        return {
            data: {
                labels: result.map(row => row.ref_practitioner || __("No Doctor")),
                datasets: [
                    {
                        name: __("Net Total"),
                        values: result.map(row => row.net_total || 0)
                    }
                ]
            },
            type: "bar",
            height: 300
        };
    },

    "onload": function(report) {
        cleanup_doctor_sales_dom(report);

        const btn = report.get_filter("refresh_data");
        if (btn && btn.$input) {
            btn.$input.off("click").on("click", function() {
                cleanup_doctor_sales_dom(report);
                report.refresh();
            });
        }
    },

    "refresh": function(report) {
        cleanup_doctor_sales_dom(report);
    },

    "after_datatable_render": function(report) {
        if (!is_correct_report(report, "Doctor Sales")) return;
    }
};

// Cleanup function for Doctor Sales report to remove unwanted sections
function cleanup_doctor_sales_dom(report) {
    const wrapper = get_safe_wrapper(report);
    if (!wrapper) return;

    wrapper.querySelector("#doctor-sales-extra-pie-section")?.remove();
    wrapper.querySelector("#opd-extra-pie-section")?.remove();
    wrapper.querySelector("#ipd-extra-chart-section")?.remove();
}

// Ensuring the correct report is loaded
function is_correct_report(report, report_name) {
    return report && report.report_name === report_name;
}

// Function to get the wrapper element of the report
function get_safe_wrapper(report) {
    if (report && report.page && report.page.wrapper && report.page.wrapper[0]) {
        return report.page.wrapper[0];
    }
    return null;
}