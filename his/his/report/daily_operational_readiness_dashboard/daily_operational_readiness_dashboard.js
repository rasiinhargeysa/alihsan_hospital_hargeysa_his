// Copyright (c) 2026, Al-Ihsan Hospital and contributors
// For license information, please see license.txt

/* eslint-disable */

const DOR_REPORT_NAME = "Daily Operational Readiness Dashboard";

const DOR_CHECKLIST_TYPES = [
    "Hospital Readiness",
    "Doctor and Nurse Availability",
    "Critical Service Availability",
    "Critical Equipment",
    "Medicine and Supply",
    "Laboratory and Diagnostic",
    "Operating Theatre and Surgery",
    "Facility and Environment",
    "Utilities and Fire Safety",
    "Patient Experience",
    "Incident and Patient Safety",
    "Problems Requiring Action",
];

frappe.query_reports[DOR_REPORT_NAME] = {
    filters: [
        {
            fieldname: "date",
            label: __("Date"),
            fieldtype: "Date",
            default: frappe.datetime.now_date(),
            reqd: 1,
        },
        {
            fieldname: "checklist_type",
            label: __("Checklist Type"),
            fieldtype: "Select",
            options: ["", ...DOR_CHECKLIST_TYPES].join("\n"),
        },
        {
            fieldname: "user",
            label: __("Responsible User"),
            fieldtype: "Link",
            options: "User",
            get_query() {
                return {
                    filters: {
                        enabled: 1,
                    },
                };
            },
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nGREEN\nAMBER\nRED\nGREY",
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        if (column.fieldname === "status") {
            return dor_status_pill(data.status);
        }

        if (column.fieldname === "compliance") {
            const amount = Number(data.compliance);
            if (!Number.isFinite(amount)) {
                return `<span style="color:#64748b;font-weight:700">—</span>`;
            }

            const color = amount >= 100
                ? "#15803d"
                : amount >= 85
                    ? "#d97706"
                    : "#b91c1c";

            return `<strong style="color:${color}">${value}</strong>`;
        }

        if (column.fieldname === "yes_count" && Number(data.yes_count || 0) > 0) {
            return `<strong style="color:#15803d">${value}</strong>`;
        }

        if (column.fieldname === "no_count" && Number(data.no_count || 0) > 0) {
            return `<strong style="color:#b91c1c">${value}</strong>`;
        }

        if (column.fieldname === "unanswered_count" && Number(data.unanswered_count || 0) > 0) {
            return `<strong style="color:#b91c1c">${value}</strong>`;
        }

        return value;
    },

    onload(report) {
        add_dor_toolbar_actions(report);
        report._dor_show_table = false;
    },

    refresh(report) {
        schedule_dor_render(report);
    },

    after_datatable_render(report) {
        schedule_dor_render(report);
        set_dor_data_table_visibility(report, Boolean(report._dor_show_table));
    },
};


function add_dor_toolbar_actions(report) {
    const set_date = (date_value) => {
        report.set_filter_value("date", date_value);
        report.refresh();
    };

    report.page.add_inner_button(
        __("Today"),
        () => set_date(frappe.datetime.now_date()),
        __("Date")
    );

    report.page.add_inner_button(
        __("Yesterday"),
        () => set_date(
            frappe.datetime.add_days(frappe.datetime.now_date(), -1)
        ),
        __("Date")
    );

    report.page.add_inner_button(
        __("Previous Day"),
        () => {
            const current = report.get_filter_value("date") || frappe.datetime.now_date();
            set_date(frappe.datetime.add_days(current, -1));
        },
        __("Date")
    );

    report.page.add_inner_button(
        __("Next Day"),
        () => {
            const current = report.get_filter_value("date") || frappe.datetime.now_date();
            set_date(frappe.datetime.add_days(current, 1));
        },
        __("Date")
    );

    report.page.add_inner_button(
        __("New Checklist"),
        () => {
            frappe.route_options = {
                date: report.get_filter_value("date") || frappe.datetime.now_date(),
            };
            frappe.new_doc("Daily Operational Readiness");
        },
        __("Actions")
    );

    report.page.add_inner_button(
        __("Open Checklist List"),
        () => frappe.set_route("List", "Daily Operational Readiness"),
        __("Actions")
    );

    report.page.add_inner_button(
        __("Show / Hide Data Table"),
        () => {
            report._dor_show_table = !report._dor_show_table;
            set_dor_data_table_visibility(report, report._dor_show_table);
        },
        __("View")
    );

    report.page.add_inner_button(
        __("Print Dashboard"),
        () => window.print(),
        __("View")
    );
}


function schedule_dor_render(report) {
    if (!report) return;

    if (report._dor_render_timer) {
        window.clearTimeout(report._dor_render_timer);
    }

    report._dor_render_timer = window.setTimeout(() => {
        render_dor_dashboard(report);
        set_dor_data_table_visibility(report, Boolean(report._dor_show_table));
    }, 80);
}


function render_dor_dashboard(report) {
    const wrapper = get_dor_wrapper(report);
    if (!wrapper) return;

    const root = wrapper.querySelector("#dor-dashboard");
    if (!root) return;

    const payload_node = root.querySelector("#dor-dashboard-payload");
    if (!payload_node) return;

    let payload = {};
    try {
        payload = JSON.parse(payload_node.textContent || "{}");
    } catch (error) {
        console.error("Unable to parse readiness dashboard payload", error);
        return;
    }

    const charts = payload.charts || {};

    bind_dor_checklist_cards(report, root);

    root.querySelectorAll(".dor-chart[data-chart-key]").forEach((host) => {
        const chart_key = host.dataset.chartKey;
        const config = charts[chart_key];

        if (!config) {
            show_dor_chart_empty(host, __("No chart configuration is available."));
            return;
        }

        const fingerprint = JSON.stringify(config);
        if (
            host.dataset.chartFingerprint === fingerprint &&
            host.querySelector("svg")
        ) {
            return;
        }

        const labels = config.data?.labels || [];
        const datasets = config.data?.datasets || [];
        const values = datasets.flatMap((dataset) => dataset.values || []);
        const is_circular = ["pie", "donut", "percentage"].includes(config.type);
        const has_non_zero = values.some((value) => Math.abs(Number(value) || 0) > 0);

        if (!labels.length || !datasets.length || (is_circular && !has_non_zero)) {
            show_dor_chart_empty(host, __("No data for the selected filters."));
            return;
        }

        host.dataset.chartFingerprint = fingerprint;
        host.innerHTML = "";

        try {
            new frappe.Chart(host, {
                data: config.data,
                type: config.type,
                height: config.height || 280,
                colors: config.colors || undefined,
                axisOptions: config.axisOptions || {
                    shortenYAxisNumbers: 1,
                    xIsSeries: config.type === "line",
                },
                barOptions: config.barOptions || {
                    spaceRatio: 0.35,
                },
                lineOptions: config.lineOptions || {
                    dotSize: 5,
                    hideDots: 0,
                    regionFill: 0,
                },
                tooltipOptions: {
                    formatTooltipX: (value) => value,
                    formatTooltipY: (value) => {
                        const number = Number(value);
                        return Number.isFinite(number)
                            ? number.toLocaleString(undefined, { maximumFractionDigits: 1 })
                            : value;
                    },
                },
            });
        } catch (error) {
            console.error(`Unable to render readiness chart ${chart_key}`, error);
            show_dor_chart_empty(
                host,
                __("This chart could not be rendered by the installed Frappe Charts version.")
            );
        }
    });
}


function bind_dor_checklist_cards(report, root) {
    root.querySelectorAll("[data-dor-checklist-type]").forEach((card) => {
        if (card.dataset.dorBound === "1") return;
        card.dataset.dorBound = "1";

        const activate = () => {
            const checklist_type = card.dataset.dorChecklistType || "";
            const current = report.get_filter_value("checklist_type") || "";
            report.set_filter_value(
                "checklist_type",
                current === checklist_type ? "" : checklist_type
            );
            report.refresh();
        };

        card.addEventListener("click", activate);
        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activate();
            }
        });
    });
}


function get_dor_wrapper(report) {
    if (
        report &&
        report.page &&
        report.page.wrapper &&
        report.page.wrapper[0]
    ) {
        return report.page.wrapper[0];
    }

    return document;
}


function set_dor_data_table_visibility(report, visible) {
    if (!report || !report.page || !report.page.main) return;

    const $main = report.page.main;
    const $wrappers = $main.find(".report-wrapper");

    if ($wrappers.length) {
        $wrappers.toggle(Boolean(visible));
    }
}


function show_dor_chart_empty(host, message) {
    host.innerHTML = `
        <div class="dor-chart-empty">
            ${frappe.utils.escape_html(String(message || ""))}
        </div>
    `;
}


function dor_status_pill(status) {
    const normalized = String(status || "GREY").toUpperCase();
    const map = {
        GREEN: { bg: "#dcfce7", fg: "#166534" },
        AMBER: { bg: "#fef3c7", fg: "#92400e" },
        RED: { bg: "#fee2e2", fg: "#991b1b" },
        GREY: { bg: "#e2e8f0", fg: "#475569" },
    };
    const style = map[normalized] || map.GREY;

    return `
        <span style="
            display:inline-flex;
            align-items:center;
            gap:5px;
            padding:3px 9px;
            border-radius:999px;
            background:${style.bg};
            color:${style.fg};
            font-size:9px;
            font-weight:800;
            letter-spacing:.25px;
        ">
            <span style="
                width:6px;
                height:6px;
                border-radius:50%;
                background:${style.fg};
            "></span>
            ${frappe.utils.escape_html(normalized)}
        </span>
    `;
}
