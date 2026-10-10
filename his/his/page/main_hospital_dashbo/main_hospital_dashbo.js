// Copyright (c) 2026, Al-Ihsan Hospital and contributors
// For license information, please see license.txt

/* eslint-disable */

const MAIN_HOSPITAL_DASHBOARD_PAGE = "main-hospital-dashbo";
const MAIN_HOSPITAL_DASHBOARD_MODULE = "HIS";
const MAIN_HOSPITAL_DASHBOARD_MODULE_PATH = "his";

const HCC_DATE_FIELDS = new Set(["from_date", "to_date", "queue_date"]);
const HCC_INVALID_DATE_VALUES = new Set([
    "", "n/a", "na", "none", "null", "undefined", "not available", "not set", "yet", "-", "—",
]);

frappe.pages[MAIN_HOSPITAL_DASHBOARD_PAGE].on_page_load = function (wrapper) {
    wrapper.main_hospital_dashboard = new MainHospitalDashboardPage(wrapper);
};

frappe.pages[MAIN_HOSPITAL_DASHBOARD_PAGE].on_page_show = function (wrapper) {
    const dashboard = wrapper.main_hospital_dashboard;
    if (!dashboard) return;

    if (!dashboard.has_loaded) {
        dashboard.refresh();
        return;
    }

    setup_section_observer(dashboard.root);
    setup_lazy_chart_rendering(dashboard.root);
};

frappe.pages[MAIN_HOSPITAL_DASHBOARD_PAGE].on_page_hide = function (wrapper) {
    const dashboard = wrapper.main_hospital_dashboard;
    if (dashboard) {
        dashboard.cancel_scheduled_refresh();
        teardown_dashboard(dashboard.root);
    }
};

class MainHospitalDashboardPage {
    constructor(wrapper) {
        this.wrapper = wrapper;
        this.wrapper.classList.add("main-hospital-dashboard-desk-page");
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __("Main Hospital Dashboard"),
            single_column: true,
        });

        add_hcc_css();

        this.controls = {};
        this.has_loaded = false;
        this.is_loading = false;
        this.refresh_timer = null;
        this.suspend_auto_refresh = false;
        this.refresh_sequence = 0;
        this.pending_refresh = false;

        this.shell = document.createElement("div");
        this.shell.className = "main-hospital-dashboard-page-shell";
        this.root = this.shell;
        this.page.main.get(0).appendChild(this.shell);

        this.add_filters();
        this.add_toolbar_actions();
        this.show_loading_state();
        this.refresh();
    }

    add_filters() {
        const schedule = () => this.schedule_refresh();

        this.controls.from_date = this.page.add_field({
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
            change: schedule,
        });

        this.controls.to_date = this.page.add_field({
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.now_date(),
            reqd: 1,
            change: schedule,
        });

        this.controls.queue_date = this.page.add_field({
            fieldname: "queue_date",
            label: __("Queue Date"),
            fieldtype: "Date",
            default: frappe.datetime.now_date(),
            reqd: 1,
            change: schedule,
        });

        this.controls.company = this.page.add_field({
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            change: schedule,
        });

        this.controls.department = this.page.add_field({
            fieldname: "department",
            label: __("Medical Department"),
            fieldtype: "Link",
            options: "Medical Department",
            change: schedule,
        });

        this.controls.practitioner = this.page.add_field({
            fieldname: "practitioner",
            label: __("Practitioner"),
            fieldtype: "Link",
            options: "Healthcare Practitioner",
            change: schedule,
        });

        this.controls.hr_department = this.page.add_field({
            fieldname: "hr_department",
            label: __("HR Department"),
            fieldtype: "Link",
            options: "Department",
            get_query: () => {
                const company = this.get_value("company");
                return company ? { filters: { company } } : {};
            },
            change: schedule,
        });

        this.controls.designation = this.page.add_field({
            fieldname: "designation",
            label: __("Designation"),
            fieldtype: "Link",
            options: "Designation",
            change: schedule,
        });

        this.controls.employment_type = this.page.add_field({
            fieldname: "employment_type",
            label: __("Employment Type"),
            fieldtype: "Link",
            options: "Employment Type",
            change: schedule,
        });

        this.controls.employee_status = this.page.add_field({
            fieldname: "employee_status",
            label: __("Employee Status"),
            fieldtype: "Select",
            options: "\nActive\nInactive\nSuspended\nLeft",
            change: schedule,
        });

        this.controls.warehouse = this.page.add_field({
            fieldname: "warehouse",
            label: __("Warehouse"),
            fieldtype: "Link",
            options: "Warehouse",
            get_query: () => {
                const company = this.get_value("company");
                const filters = { disabled: 0 };
                if (company) filters.company = company;
                return { filters };
            },
            change: schedule,
        });

        this.controls.cost_center = this.page.add_field({
            fieldname: "cost_center",
            label: __("Cost Center"),
            fieldtype: "Link",
            options: "Cost Center",
            get_query: () => ({
                filters: {
                    company: this.get_value("company") || "",
                    is_group: 0,
                },
            }),
            change: schedule,
        });

        this.controls.customer = this.page.add_field({
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
            change: schedule,
        });

        this.controls.sales_status = this.page.add_field({
            fieldname: "sales_status",
            label: __("Invoice Status"),
            fieldtype: "Select",
            options: "\nPaid\nUnpaid\nPartly Paid\nOverdue\nCancelled\nReturn",
            change: schedule,
        });

        this.controls.finance_book = this.page.add_field({
            fieldname: "finance_book",
            label: __("Finance Book"),
            fieldtype: "Link",
            options: "Finance Book",
            change: schedule,
        });
    }

    add_toolbar_actions() {
        this.refresh_button = this.page.set_primary_action(
            __("Refresh"),
            () => this.refresh({ force: true }),
            "refresh"
        );

        this.page.set_secondary_action(
            __("Print"),
            () => window.print(),
            "printer"
        );

        const set_period = async (from_date, to_date) => {
            this.suspend_auto_refresh = true;
            try {
                await Promise.all([
                    Promise.resolve(this.controls.from_date.set_value(from_date)),
                    Promise.resolve(this.controls.to_date.set_value(to_date)),
                ]);
            } finally {
                this.suspend_auto_refresh = false;
            }
            this.refresh({ force: true });
        };

        this.page.add_inner_button(__("Today"), () => {
            const today = frappe.datetime.now_date();
            set_period(today, today);
        }, __("Period"));

        this.page.add_inner_button(__("This Week"), () => {
            set_period(frappe.datetime.week_start(), frappe.datetime.week_end());
        }, __("Period"));

        this.page.add_inner_button(__("This Month"), () => {
            set_period(frappe.datetime.month_start(), frappe.datetime.month_end());
        }, __("Period"));

        this.page.add_inner_button(__("This Quarter"), () => {
            set_period(frappe.datetime.quarter_start(), frappe.datetime.quarter_end());
        }, __("Period"));

        [
            "OPD Report",
            "Queue Time Management",
            "IPD Report",
            "OT Form Report",
            "Lab Result Analytics",
            "Employee List Report",
            "Employee Attendance Report",
            "Total Stock Summary",
            "Sales Dashboard",
            "Financial Dashboard",
        ].forEach((report_name) => {
            this.page.add_menu_item(__(`Open ${report_name}`), () => {
                open_source_report(this, report_name);
            });
        });
    }

    get_value(fieldname) {
        const control = this.controls[fieldname];
        return control ? control.get_value() : null;
    }

    async repair_invalid_date_controls() {
        const defaults = {
            from_date: frappe.datetime.month_start(),
            to_date: frappe.datetime.now_date(),
            queue_date: frappe.datetime.now_date(),
        };

        const repairs = [];
        this.suspend_auto_refresh = true;
        try {
            HCC_DATE_FIELDS.forEach((fieldname) => {
                const control = this.controls[fieldname];
                if (!control || normalize_dashboard_date(control.get_value())) return;
                repairs.push(Promise.resolve(control.set_value(defaults[fieldname])));
            });
            if (repairs.length) await Promise.all(repairs);
        } finally {
            this.suspend_auto_refresh = false;
        }
    }

    get_filter_values() {
        const values = {};
        Object.keys(this.controls).forEach((fieldname) => {
            let value = this.get_value(fieldname);

            if (HCC_DATE_FIELDS.has(fieldname)) {
                const normalized = normalize_dashboard_date(value);
                if (normalized) {
                    values[fieldname] = normalized;
                } else {
                    // Repair stale browser/local-storage values such as N/A.
                    values[fieldname] = fieldname === "from_date"
                        ? frappe.datetime.month_start()
                        : frappe.datetime.now_date();
                }
                return;
            }

            if (value !== undefined && value !== null && value !== "") {
                values[fieldname] = value;
            }
        });
        return values;
    }

    schedule_refresh() {
        if (this.suspend_auto_refresh || !this.has_loaded) return;
        if (this.is_loading) {
            this.pending_refresh = true;
            return;
        }
        this.cancel_scheduled_refresh();
        this.refresh_timer = window.setTimeout(() => this.refresh(), 500);
    }

    cancel_scheduled_refresh() {
        if (this.refresh_timer) {
            window.clearTimeout(this.refresh_timer);
            this.refresh_timer = null;
        }
    }

    validate_filters(filters) {
        if (!is_valid_dashboard_date(filters.from_date) || !is_valid_dashboard_date(filters.to_date)) {
            frappe.msgprint(__("From Date and To Date must be valid dates."));
            return false;
        }
        if (!is_valid_dashboard_date(filters.queue_date)) {
            frappe.msgprint(__("Queue Date must be a valid date."));
            return false;
        }
        if (String(filters.from_date) > String(filters.to_date)) {
            frappe.msgprint(__("From Date cannot be after To Date."));
            return false;
        }
        return true;
    }


    get_api_method() {
        if (window.MAIN_HOSPITAL_DASHBOARD_API_METHOD) {
            return window.MAIN_HOSPITAL_DASHBOARD_API_METHOD;
        }

        const module_map = (frappe.boot && frappe.boot.module_app) || {};
        const app_name =
            module_map[MAIN_HOSPITAL_DASHBOARD_MODULE] ||
            module_map[MAIN_HOSPITAL_DASHBOARD_MODULE.toLowerCase()] ||
            module_map.His;

        if (!app_name) {
            throw new Error(
                `Unable to resolve the app for module ${MAIN_HOSPITAL_DASHBOARD_MODULE}. ` +
                "Set window.MAIN_HOSPITAL_DASHBOARD_API_METHOD to the full Python method path."
            );
        }

        return `${app_name}.${MAIN_HOSPITAL_DASHBOARD_MODULE_PATH}.page.main_hospital_dashbo.main_hospital_dashbo.get_dashboard_data`;
    }

    async refresh(options = {}) {
        this.cancel_scheduled_refresh();
        if (this.is_loading) return;

        const request_sequence = ++this.refresh_sequence;
        this.is_loading = true;
        this.set_refresh_button_state(true);
        this.page.set_indicator(__("Refreshing"), "orange");
        if (!this.has_loaded) this.show_loading_state();

        try {
            await this.repair_invalid_date_controls();
            const filters = this.get_filter_values();
            if (!this.validate_filters(filters)) return;

            const response = await frappe.call({
                method: this.get_api_method(),
                args: {
                    filters: JSON.stringify(filters),
                    force_refresh: options.force ? 1 : 0,
                },
                freeze: false,
            });

            if (request_sequence !== this.refresh_sequence) return;

            const result = response && response.message;
            if (!result || !result.html) {
                throw new Error(__("The dashboard API returned no HTML."));
            }

            teardown_dashboard(this.root);
            this.shell.innerHTML = result.html;
            this.root = this.shell.querySelector("#main-hospital-dashboard") || this.shell;
            this.has_loaded = true;
            render_dashboard(this);

            const refreshed_at = format_dashboard_datetime(result.generated_at);
            const duration = Number(result.duration_ms);
            const cache_hits = Array.isArray(result.cache_hits) ? result.cache_hits.length : 0;
            const subtitle_parts = [];
            if (refreshed_at) subtitle_parts.push(__("Updated {0}", [refreshed_at]));
            if (Number.isFinite(duration)) subtitle_parts.push(__('{0} ms', [duration.toLocaleString()]));
            if (cache_hits) subtitle_parts.push(__('{0} cached sections', [cache_hits]));
            this.page.set_title_sub(subtitle_parts.join(" · "));
        } catch (error) {
            console.error("Main Hospital Dashboard refresh failed", error);
            this.show_error_state(error);
            this.page.set_indicator(__("Unavailable"), "red");
        } finally {
            if (request_sequence === this.refresh_sequence) {
                this.is_loading = false;
                this.set_refresh_button_state(false);
                if (this.pending_refresh) {
                    this.pending_refresh = false;
                    this.schedule_refresh();
                }
            }
        }
    }


    update_page_indicator(overall) {
        const status = String(overall.status || "GREY").toUpperCase();
        const colors = {
            GREEN: "green",
            AMBER: "orange",
            RED: "red",
            BLUE: "blue",
            GREY: "grey",
        };
        this.page.set_indicator(status, colors[status] || "grey");
    }

    set_refresh_button_state(disabled) {
        if (this.refresh_button && this.refresh_button.prop) {
            this.refresh_button.prop("disabled", Boolean(disabled));
        }
    }

    show_loading_state() {
        this.shell.innerHTML = `
            <div class="main-hospital-dashboard-page-state">
                <span class="hcc-page-spinner" aria-hidden="true"></span>
                <strong>${__("Loading Main Hospital Dashboard")}</strong>
                <span>${__("Calculating OPD, Queue Time, IPD, OT, Laboratory, Pharmacy, Warehouse, Optical, HR attendance, Inventory, Sales and Finance sections.")}</span>
            </div>
        `;
        this.root = this.shell;
    }

    show_error_state(error) {
        const message =
            (error && (error.message || error.exc_type)) ||
            __("The dashboard could not be loaded.");

        this.shell.innerHTML = `
            <div class="main-hospital-dashboard-page-state">
                <strong>${__("Main Hospital Dashboard could not load")}</strong>
                <span>${frappe.utils.escape_html(String(message))}</span>
                <button type="button" class="btn btn-primary hcc-retry-dashboard">
                    ${__("Try Again")}
                </button>
            </div>
        `;
        this.root = this.shell;
        this.shell.querySelector(".hcc-retry-dashboard")?.addEventListener(
            "click",
            () => this.refresh()
        );
    }
}

function render_dashboard(report) {
    const root = report && report.root;
    if (!root) return;

    const payload_node = root.querySelector("#hcc-chart-payload");
    if (!payload_node) return;

    let payload;
    try {
        payload = JSON.parse(payload_node.textContent || "{}");
    } catch (error) {
        console.error("Unable to parse main-hospital-dashboard chart payload", error);
        return;
    }

    root._hcc_payload = payload;
    if (report && typeof report.update_page_indicator === "function") {
        report.update_page_indicator(payload.overall || {});
    }

    const render_id = payload.render_id || "current";
    if (root.dataset.hccBound !== render_id) {
        root.dataset.hccBound = render_id;
        bind_section_navigation(root, report);
        bind_source_report_links(root, report);
        bind_keyboard_cards(root, report);
        setup_section_observer(root);
    }

    window.requestAnimationFrame(() => {
        setup_lazy_chart_rendering(root);
    });
}


function bind_section_navigation(root, report) {
    root.querySelectorAll("[data-hcc-target]").forEach((button) => {
        button.addEventListener("click", () => {
            scroll_to_section(root, report, button.dataset.hccTarget);
        });
    });

    root.querySelectorAll("[data-hcc-open-tab]").forEach((element) => {
        element.addEventListener("click", (event) => {
            if (event.target.closest("[data-hcc-report]")) return;
            scroll_to_section(root, report, element.dataset.hccOpenTab);
        });
    });
}


function bind_keyboard_cards(root, report) {
    root.querySelectorAll('[role="button"][data-hcc-open-tab]').forEach((card) => {
        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                scroll_to_section(root, report, card.dataset.hccOpenTab);
            }
        });
    });
}


function scroll_to_section(root, report, section_name) {
    const section = root.querySelector(`[data-hcc-section="${css_escape(section_name)}"]`);
    if (!section) return;

    report._hcc_active_section = section_name;
    set_active_section_nav(root, section_name);
    render_charts_in_section(root, section);
    section.scrollIntoView({ behavior: "smooth", block: "start" });
}


function set_active_section_nav(root, section_name) {
    root.querySelectorAll(".hcc-tab").forEach((button) => {
        const active = button.dataset.hccTarget === section_name;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-current", active ? "true" : "false");
    });
}


function setup_section_observer(root) {
    if (root._hcc_section_observer) {
        root._hcc_section_observer.disconnect();
    }

    const sections = [...root.querySelectorAll(".hcc-section[data-hcc-section]")];
    if (!sections.length || !("IntersectionObserver" in window)) return;

    root._hcc_section_observer = new IntersectionObserver((entries) => {
        const visible = entries
            .filter((entry) => entry.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio);

        if (visible.length) {
            set_active_section_nav(root, visible[0].target.dataset.hccSection);
            visible.forEach((entry) => render_charts_in_section(root, entry.target));
        }
    }, {
        root: null,
        rootMargin: "-18% 0px -65% 0px",
        threshold: [0.05, 0.2, 0.5],
    });

    sections.forEach((section) => root._hcc_section_observer.observe(section));
}


function setup_lazy_chart_rendering(root) {
    if (!root) return;

    if (root._hcc_chart_observer) {
        root._hcc_chart_observer.disconnect();
    }

    const hosts = [...root.querySelectorAll(".hcc-chart[data-chart-key]")];
    if (!hosts.length) return;

    if (!("IntersectionObserver" in window)) {
        render_chart_hosts_in_batches(root, hosts);
        return;
    }

    root._hcc_chart_observer = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            schedule_chart_render(root, entry.target);
        });
    }, {
        root: null,
        rootMargin: "350px 0px 350px 0px",
        threshold: 0.01,
    });

    hosts.forEach((host) => root._hcc_chart_observer.observe(host));

    // Overview and first visible charts should appear immediately.
    hosts.slice(0, 3).forEach((host) => schedule_chart_render(root, host));

    if (!root._hcc_print_handler) {
        root._hcc_print_handler = () => render_all_charts(root);
        window.addEventListener("beforeprint", root._hcc_print_handler);
    }
}


function render_charts_in_section(root, section) {
    if (!root || !section) return;
    section.querySelectorAll(".hcc-chart[data-chart-key]").forEach((host) => {
        if (root._hcc_chart_observer) root._hcc_chart_observer.unobserve(host);
        schedule_chart_render(root, host);
    });
}


function render_chart_hosts_in_batches(root, hosts) {
    const queue = [...hosts];
    const run_batch = () => {
        queue.splice(0, 3).forEach((host) => render_chart_host(root, host));
        if (queue.length) window.setTimeout(run_batch, 16);
    };
    run_batch();
}


function schedule_chart_render(root, host) {
    if (!host || host.dataset.chartScheduled === "1") return;
    host.dataset.chartScheduled = "1";

    const task = () => {
        host.dataset.chartScheduled = "0";
        render_chart_host(root, host);
    };

    if ("requestIdleCallback" in window) {
        window.requestIdleCallback(task, { timeout: 500 });
    } else {
        window.setTimeout(task, 0);
    }
}


function render_all_charts(root) {
    if (!root) return;
    root.querySelectorAll(".hcc-chart[data-chart-key]").forEach((host) => {
        render_chart_host(root, host);
    });
}


function render_chart_host(root, host) {
    const payload = root._hcc_payload || {};
    const charts = payload.charts || {};
    const key = host.dataset.chartKey;
    const config = charts[key];

    if (!config) {
        show_chart_empty(host, __("No chart data is available."));
        return;
    }

    const fingerprint = JSON.stringify(config);
    if (host.dataset.chartFingerprint === fingerprint && host.querySelector("svg")) {
        return;
    }

    host.dataset.chartFingerprint = fingerprint;
    host.innerHTML = "";

    const labels = (config.data && config.data.labels) || [];
    const datasets = (config.data && config.data.datasets) || [];
    const has_values = datasets.some((dataset) =>
        (dataset.values || []).some((value) => Number(value) !== 0)
    );

    if (!labels.length || !datasets.length || !has_values) {
        show_chart_empty(host, __("No data for the selected filters."));
        return;
    }

    try {
        new frappe.Chart(host, {
            data: config.data,
            type: config.type,
            height: config.height || 280,
            colors: config.colors || undefined,
            maxSlices: config.maxSlices || 10,
            axisOptions: config.axisOptions || { shortenYAxisNumbers: 1 },
            barOptions: config.barOptions || { spaceRatio: 0.35 },
            tooltipOptions: {
                formatTooltipX: (value) => value,
                formatTooltipY: (value) => format_chart_value(value),
            },
        });
    } catch (error) {
        console.error(`Unable to render chart ${key}`, error);
        show_chart_empty(host, __("This chart could not be rendered on the installed Frappe Charts version."));
    }
}


function show_chart_empty(host, message) {
    host.innerHTML = `<div class="hcc-chart-empty">${frappe.utils.escape_html(message)}</div>`;
}


function format_chart_value(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return value;
    return number.toLocaleString(undefined, {
        maximumFractionDigits: Math.abs(number) < 100 ? 2 : 0,
    });
}


function bind_source_report_links(root, report) {
    root.querySelectorAll("[data-hcc-report]").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            open_source_report(report, button.dataset.hccReport);
        });
    });
}


function open_source_report(report, report_name) {
    if (!report_name) return;

    const current = report.get_filter_values ? report.get_filter_values() : {};
    let target = {};

    const copy = (source_name, target_name = source_name) => {
        if (current[source_name] !== undefined && current[source_name] !== null && current[source_name] !== "") {
            target[target_name] = current[source_name];
        }
    };

    if (report_name === "OPD Report") {
        ["from_date", "to_date", "practitioner", "department"].forEach((field) => copy(field));
    } else if (report_name === "Queue Time Management") {
        copy("queue_date", "date");
        copy("practitioner");
        copy("department");
    } else if (report_name === "IPD Report") {
        ["from_date", "to_date", "practitioner", "department"].forEach((field) => copy(field));
    } else if (report_name === "OT Form Report") {
        copy("from_date", "Start_date");
        copy("to_date", "End_date");
        copy("practitioner", "doctor");
    } else if (report_name === "Lab Result Analytics") {
        ["company", "from_date", "to_date", "department", "practitioner"].forEach((field) => copy(field));
    } else if (report_name === "Employee List Report") {
        copy("company");
        copy("hr_department", "department");
        copy("designation");
        copy("employment_type");
        copy("employee_status", "status");
    } else if (report_name === "Employee Attendance Report") {
        copy("queue_date", "from_date");
        copy("queue_date", "to_date");
        copy("hr_department", "department");
    } else if (report_name === "Total Stock Summary") {
        target.group_by = "Warehouse";
        copy("company");
        copy("warehouse");
    } else if (report_name === "Sales Dashboard") {
        ["from_date", "to_date", "company", "cost_center", "customer"].forEach((field) => copy(field));
        copy("sales_status", "status");
    } else if (report_name === "Financial Dashboard") {
        ["from_date", "to_date", "company", "cost_center", "finance_book"].forEach((field) => copy(field));
    } else {
        ["from_date", "to_date", "company"].forEach((field) => copy(field));
    }

    frappe.set_route("query-report", report_name, target);
}


function normalize_dashboard_date(value) {
    if (value === null || value === undefined) return null;
    const text = String(value).trim();
    if (HCC_INVALID_DATE_VALUES.has(text.toLowerCase())) return null;

    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return null;

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const probe = new Date(Date.UTC(year, month - 1, day));
    if (
        probe.getUTCFullYear() !== year ||
        probe.getUTCMonth() !== month - 1 ||
        probe.getUTCDate() !== day
    ) {
        return null;
    }

    return `${match[1]}-${match[2]}-${match[3]}`;
}


function is_valid_dashboard_date(value) {
    return Boolean(normalize_dashboard_date(value));
}


function format_dashboard_datetime(value) {
    if (value === null || value === undefined) return "";
    const text = String(value).trim();
    if (!text || HCC_INVALID_DATE_VALUES.has(text.toLowerCase())) return "";

    const normalized = text.replace("T", " ").replace(/Z$/, "");
    if (!/^\d{4}-\d{2}-\d{2}/.test(normalized)) return "";

    try {
        return frappe.datetime.str_to_user(normalized);
    } catch (error) {
        console.warn("Unable to format dashboard timestamp", value, error);
        return normalized.slice(0, 19);
    }
}


function teardown_dashboard(root) {
    if (!root) return;
    if (root._hcc_section_observer) root._hcc_section_observer.disconnect();
    if (root._hcc_chart_observer) root._hcc_chart_observer.disconnect();
    if (root._hcc_print_handler) {
        window.removeEventListener("beforeprint", root._hcc_print_handler);
        root._hcc_print_handler = null;
    }
}


function css_escape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
        return window.CSS.escape(String(value));
    }
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}


function add_hcc_css() {
    if (document.getElementById("main-hospital-dashboard-css")) return;

    const style = document.createElement("style");
    style.id = "main-hospital-dashboard-css";
    style.textContent = `
        .main-hospital-dashboard-desk-page .layout-main-section {
            max-width: none !important;
            padding: 0 !important;
        }

        .main-hospital-dashboard-page-shell {
            min-height: 360px;
            padding: 16px 18px 28px;
            background: var(--bg-color, #f4f7fb);
        }

        .main-hospital-dashboard-page-state {
            min-height: 320px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 12px;
            border: 1px solid var(--border-color, #dbe4ee);
            border-radius: 16px;
            background: var(--card-bg, #ffffff);
            color: var(--text-muted, #64748b);
            text-align: center;
            padding: 32px;
        }

        .main-hospital-dashboard-page-state strong {
            color: var(--text-color, #172033);
            font-size: 16px;
        }

        .main-hospital-dashboard-page-state .hcc-page-spinner {
            width: 34px;
            height: 34px;
            border: 3px solid var(--border-color, #dbe4ee);
            border-top-color: var(--primary, #2563eb);
            border-radius: 50%;
            animation: hcc-page-spin 0.8s linear infinite;
        }

        @keyframes hcc-page-spin {
            to { transform: rotate(360deg); }
        }

        .hcc-html-only-report .datatable,
        .hcc-html-only-report .chart-wrapper,
        .hcc-html-only-report .report-summary,
        .hcc-html-only-report .summary-wrapper {
            display: none !important;
        }

        .hcc-html-only-report .report-message {
            display: block !important;
            max-width: none !important;
            padding: 0 !important;
            margin: 0 !important;
            text-align: initial !important;
            color: inherit !important;
        }

        #main-hospital-dashboard {
            --hcc-bg: #f4f7fb;
            --hcc-card: #ffffff;
            --hcc-card-soft: #f8fafc;
            --hcc-border: #dbe4ee;
            --hcc-text: #172033;
            --hcc-muted: #64748b;
            --hcc-blue: #2563eb;
            --hcc-green: #16a34a;
            --hcc-amber: #d97706;
            --hcc-red: #dc2626;
            --hcc-grey: #64748b;
            --hcc-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
            color: var(--hcc-text);
            font-size: 13px;
            line-height: 1.5;
            margin: 0 0 18px;
        }

        html[data-theme="dark"] #main-hospital-dashboard,
        body[data-theme="dark"] #main-hospital-dashboard {
            --hcc-bg: #111827;
            --hcc-card: #182233;
            --hcc-card-soft: #202b3d;
            --hcc-border: #334155;
            --hcc-text: #f8fafc;
            --hcc-muted: #a8b3c4;
            --hcc-shadow: 0 12px 36px rgba(0, 0, 0, 0.28);
        }

        #main-hospital-dashboard,
        #main-hospital-dashboard * {
            box-sizing: border-box;
        }

        #main-hospital-dashboard button {
            font: inherit;
        }

        #main-hospital-dashboard .hcc-hero {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(240px, 330px);
            gap: 20px;
            align-items: stretch;
            border: 1px solid #1e3a5f;
            border-radius: 20px;
            padding: 26px;
            color: #ffffff;
            background:
                radial-gradient(circle at 85% 10%, rgba(34, 197, 94, 0.28), transparent 28%),
                linear-gradient(135deg, #0f2743 0%, #174b73 55%, #0f766e 100%);
            box-shadow: var(--hcc-shadow);
            overflow: hidden;
            position: relative;
        }

        #main-hospital-dashboard .hcc-hero::after {
            content: "";
            position: absolute;
            width: 220px;
            height: 220px;
            right: -80px;
            bottom: -120px;
            border: 32px solid rgba(255, 255, 255, 0.07);
            border-radius: 50%;
            pointer-events: none;
        }

        #main-hospital-dashboard .hcc-hero-copy,
        #main-hospital-dashboard .hcc-overall {
            position: relative;
            z-index: 1;
        }

        #main-hospital-dashboard .hcc-eyebrow,
        #main-hospital-dashboard .hcc-section-kicker {
            font-size: 10px;
            letter-spacing: 0.14em;
            font-weight: 800;
            text-transform: uppercase;
        }

        #main-hospital-dashboard .hcc-eyebrow {
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 8px;
        }

        #main-hospital-dashboard .hcc-hero h1 {
            font-size: clamp(23px, 3vw, 34px);
            line-height: 1.15;
            margin: 0 0 10px;
            color: #ffffff;
            font-weight: 750;
            letter-spacing: -0.035em;
        }

        #main-hospital-dashboard .hcc-hero p {
            color: rgba(255, 255, 255, 0.78);
            margin: 0;
            max-width: 740px;
            font-size: 14px;
        }

        #main-hospital-dashboard .hcc-meta-row {
            display: flex;
            flex-wrap: wrap;
            gap: 9px;
            margin-top: 18px;
        }

        #main-hospital-dashboard .hcc-meta-row span {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 999px;
            padding: 5px 10px;
            color: rgba(255, 255, 255, 0.78);
            font-size: 11px;
        }

        #main-hospital-dashboard .hcc-meta-row strong {
            color: #ffffff;
            font-weight: 700;
        }

        #main-hospital-dashboard .hcc-overall {
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 16px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            background: rgba(5, 17, 33, 0.26);
            backdrop-filter: blur(8px);
        }

        #main-hospital-dashboard .hcc-overall::before {
            content: "";
            width: 11px;
            height: 11px;
            border-radius: 50%;
            margin-bottom: 12px;
            box-shadow: 0 0 0 6px rgba(255,255,255,.08);
        }

        #main-hospital-dashboard .hcc-overall.hcc-status-red::before { background: #f87171; }
        #main-hospital-dashboard .hcc-overall.hcc-status-amber::before { background: #fbbf24; }
        #main-hospital-dashboard .hcc-overall.hcc-status-green::before { background: #4ade80; }
        #main-hospital-dashboard .hcc-overall.hcc-status-grey::before { background: #cbd5e1; }

        #main-hospital-dashboard .hcc-overall-kicker {
            color: rgba(255,255,255,.68);
            text-transform: uppercase;
            letter-spacing: .1em;
            font-size: 9px;
            font-weight: 800;
        }

        #main-hospital-dashboard .hcc-overall strong {
            font-size: 34px;
            line-height: 1;
            margin: 6px 0 7px;
            color: #ffffff;
        }

        #main-hospital-dashboard .hcc-overall > span:not(.hcc-overall-kicker) {
            font-size: 14px;
            font-weight: 700;
        }

        #main-hospital-dashboard .hcc-overall small {
            color: rgba(255,255,255,.7);
            margin-top: 5px;
        }

        #main-hospital-dashboard .hcc-notices {
            margin: 12px 0 0;
            border: 1px solid #cbd5e1;
            background: var(--hcc-card-soft);
            border-radius: 12px;
            padding: 10px 13px;
            color: var(--hcc-muted);
        }

        #main-hospital-dashboard .hcc-notices summary {
            cursor: pointer;
            font-weight: 700;
            color: var(--hcc-text);
        }

        #main-hospital-dashboard .hcc-notices ul {
            margin: 9px 0 0 18px;
            padding: 0;
        }

        #main-hospital-dashboard .hcc-executive-strip {
            margin: 14px 0;
            padding: 16px;
            border: 1px solid var(--hcc-border);
            border-radius: 16px;
            background: var(--hcc-card);
            box-shadow: 0 5px 18px rgba(15, 23, 42, 0.05);
        }

        #main-hospital-dashboard .hcc-strip-heading {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 11px;
        }

        #main-hospital-dashboard .hcc-strip-heading .hcc-section-kicker {
            color: var(--hcc-blue);
        }

        #main-hospital-dashboard .hcc-strip-heading h2 {
            margin: 2px 0 0;
            font-size: 17px;
            color: var(--hcc-text);
        }

        #main-hospital-dashboard .hcc-strip-note {
            color: var(--hcc-muted);
            font-size: 10px;
            text-align: right;
        }

        #main-hospital-dashboard .hcc-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
            gap: 12px;
            margin: 14px 0;
        }

        #main-hospital-dashboard .hcc-kpi {
            background: var(--hcc-card);
            border: 1px solid var(--hcc-border);
            border-top: 4px solid var(--hcc-blue);
            border-radius: 15px;
            padding: 15px 16px 13px;
            min-height: 115px;
            box-shadow: 0 5px 16px rgba(15, 23, 42, 0.05);
            transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
            cursor: pointer;
            outline: none;
        }

        #main-hospital-dashboard .hcc-kpi:hover,
        #main-hospital-dashboard .hcc-kpi:focus-visible {
            transform: translateY(-3px);
            box-shadow: var(--hcc-shadow);
        }

        #main-hospital-dashboard .hcc-kpi.hcc-status-green { border-top-color: var(--hcc-green); }
        #main-hospital-dashboard .hcc-kpi.hcc-status-amber { border-top-color: var(--hcc-amber); }
        #main-hospital-dashboard .hcc-kpi.hcc-status-red { border-top-color: var(--hcc-red); }
        #main-hospital-dashboard .hcc-kpi.hcc-status-grey { border-top-color: var(--hcc-grey); }

        #main-hospital-dashboard .hcc-kpi-label {
            color: var(--hcc-muted);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .045em;
        }

        #main-hospital-dashboard .hcc-kpi-value {
            color: var(--hcc-text);
            font-size: clamp(16px, 1.45vw, 22px);
            font-weight: 760;
            letter-spacing: -0.035em;
            margin: 9px 0 8px;
            overflow-wrap: normal;
            word-break: normal;
            white-space: nowrap;
        }

        #main-hospital-dashboard .hcc-kpi-foot {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            color: var(--hcc-muted);
            font-size: 10px;
        }

        #main-hospital-dashboard .hcc-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--hcc-blue);
            flex: 0 0 auto;
        }

        #main-hospital-dashboard .hcc-status-green .hcc-dot { background: var(--hcc-green); }
        #main-hospital-dashboard .hcc-status-amber .hcc-dot { background: var(--hcc-amber); }
        #main-hospital-dashboard .hcc-status-red .hcc-dot { background: var(--hcc-red); }
        #main-hospital-dashboard .hcc-status-grey .hcc-dot { background: var(--hcc-grey); }

        #main-hospital-dashboard .hcc-tabs {
            display: flex;
            gap: 5px;
            align-items: center;
            overflow-x: auto;
            padding: 6px;
            margin: 14px 0;
            border: 1px solid var(--hcc-border);
            border-radius: 13px;
            background: var(--hcc-card);
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
            position: sticky;
            top: 8px;
            z-index: 25;
            backdrop-filter: blur(12px);
        }

        #main-hospital-dashboard .hcc-tab {
            border: 0;
            border-radius: 9px;
            padding: 8px 14px;
            background: transparent;
            color: var(--hcc-muted);
            font-weight: 700;
            white-space: nowrap;
            cursor: pointer;
        }

        #main-hospital-dashboard .hcc-tab:hover {
            background: var(--hcc-card-soft);
            color: var(--hcc-text);
        }

        #main-hospital-dashboard .hcc-tab.is-active {
            background: #e8f0ff;
            color: #1d4ed8;
            box-shadow: inset 0 0 0 1px #bfdbfe;
        }

        html[data-theme="dark"] #main-hospital-dashboard .hcc-tab.is-active,
        body[data-theme="dark"] #main-hospital-dashboard .hcc-tab.is-active {
            background: #1e3a5f;
            color: #bfdbfe;
            box-shadow: inset 0 0 0 1px #315a88;
        }

        #main-hospital-dashboard .hcc-panel {
            background: var(--hcc-card);
            border: 1px solid var(--hcc-border);
            border-radius: 16px;
            box-shadow: 0 6px 20px rgba(15, 23, 42, 0.05);
            overflow: hidden;
        }

        #main-hospital-dashboard .hcc-action-panel {
            margin-bottom: 14px;
        }

        #main-hospital-dashboard .hcc-panel-head,
        #main-hospital-dashboard .hcc-card-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding: 15px 17px;
            border-bottom: 1px solid var(--hcc-border);
            background: var(--hcc-card-soft);
        }

        #main-hospital-dashboard .hcc-panel-head h2,
        #main-hospital-dashboard .hcc-card-heading h3 {
            margin: 0;
            color: var(--hcc-text);
            line-height: 1.25;
        }

        #main-hospital-dashboard .hcc-panel-head h2 { font-size: 16px; }
        #main-hospital-dashboard .hcc-card-heading h3 { font-size: 14px; }

        #main-hospital-dashboard .hcc-panel-head .hcc-section-kicker {
            color: var(--hcc-red);
        }

        #main-hospital-dashboard .hcc-card-heading p {
            margin: 3px 0 0;
            color: var(--hcc-muted);
            font-size: 11px;
        }

        #main-hospital-dashboard .hcc-count-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 30px;
            height: 30px;
            padding: 0 9px;
            border-radius: 999px;
            background: #fee2e2;
            color: #991b1b;
            font-weight: 800;
        }

        #main-hospital-dashboard .hcc-alert-list {
            display: grid;
        }

        #main-hospital-dashboard .hcc-alert-row {
            display: grid;
            grid-template-columns: 58px minmax(0, 1fr) 110px auto;
            gap: 12px;
            align-items: center;
            padding: 13px 17px;
            border-bottom: 1px solid var(--hcc-border);
        }

        #main-hospital-dashboard .hcc-alert-row:last-child { border-bottom: 0; }

        #main-hospital-dashboard .hcc-alert-severity,
        #main-hospital-dashboard .hcc-status-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            padding: 3px 8px;
            font-size: 9px;
            font-weight: 800;
            letter-spacing: .05em;
        }

        #main-hospital-dashboard .hcc-alert-row.hcc-status-red .hcc-alert-severity,
        #main-hospital-dashboard .hcc-source-card.hcc-status-red .hcc-status-pill {
            background: #fee2e2; color: #991b1b;
        }

        #main-hospital-dashboard .hcc-alert-row.hcc-status-amber .hcc-alert-severity,
        #main-hospital-dashboard .hcc-source-card.hcc-status-amber .hcc-status-pill {
            background: #fef3c7; color: #92400e;
        }

        #main-hospital-dashboard .hcc-source-card.hcc-status-green .hcc-status-pill {
            background: #dcfce7; color: #166534;
        }

        #main-hospital-dashboard .hcc-source-card.hcc-status-grey .hcc-status-pill {
            background: #e2e8f0; color: #475569;
        }

        #main-hospital-dashboard .hcc-alert-copy {
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        #main-hospital-dashboard .hcc-alert-copy strong { color: var(--hcc-text); }
        #main-hospital-dashboard .hcc-alert-copy span { color: var(--hcc-muted); font-size: 11px; }
        #main-hospital-dashboard .hcc-alert-domain { color: var(--hcc-muted); font-weight: 700; font-size: 11px; }

        #main-hospital-dashboard .hcc-link-button {
            border: 0;
            background: transparent;
            color: var(--hcc-blue);
            font-size: 11px;
            font-weight: 750;
            padding: 4px 0;
            cursor: pointer;
            white-space: nowrap;
        }

        #main-hospital-dashboard .hcc-link-button:hover { text-decoration: underline; }

        #main-hospital-dashboard .hcc-section {
            display: block;
            animation: hcc-fade-in .18s ease;
            scroll-margin-top: 82px;
            margin: 0 0 18px;
            padding: 17px;
            border: 1px solid var(--hcc-border);
            border-top: 4px solid var(--hcc-blue);
            border-radius: 18px;
            background: var(--hcc-bg);
        }

        #main-hospital-dashboard .hcc-section-overview { border-top-color: #0f766e; }
        #main-hospital-dashboard .hcc-section-opd { border-top-color: #2563eb; }
        #main-hospital-dashboard .hcc-section-queue { border-top-color: #0ea5e9; }
        #main-hospital-dashboard .hcc-section-ipd { border-top-color: #7c3aed; }
        #main-hospital-dashboard .hcc-section-ot { border-top-color: #db2777; }
        #main-hospital-dashboard .hcc-section-lab { border-top-color: #0891b2; }
        #main-hospital-dashboard .hcc-section-hr { border-top-color: #9333ea; }
        #main-hospital-dashboard .hcc-section-inventory { border-top-color: #ea580c; }
        #main-hospital-dashboard .hcc-section-sales { border-top-color: #16a34a; }
        #main-hospital-dashboard .hcc-section-finance { border-top-color: #d97706; }

        @keyframes hcc-fade-in {
            from { opacity: 0; transform: translateY(3px); }
            to { opacity: 1; transform: translateY(0); }
        }

        #main-hospital-dashboard .hcc-inline-actions {
            display: flex;
            justify-content: flex-end;
            margin: 4px 0 12px;
        }

        #main-hospital-dashboard .hcc-section-heading {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            margin: 8px 2px 13px;
        }

        #main-hospital-dashboard .hcc-section-title-row {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            min-width: 0;
        }

        #main-hospital-dashboard .hcc-section-icon {
            width: 42px;
            height: 42px;
            flex: 0 0 42px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 12px;
            border: 1px solid var(--hcc-border);
            background: var(--hcc-card);
            font-size: 20px;
            box-shadow: 0 4px 12px rgba(15, 23, 42, .05);
        }

        #main-hospital-dashboard .hcc-section-heading .hcc-section-kicker {
            color: var(--hcc-blue);
        }
        #main-hospital-dashboard .hcc-section-opd .hcc-section-kicker { color: #2563eb; }
        #main-hospital-dashboard .hcc-section-queue .hcc-section-kicker { color: #0ea5e9; }
        #main-hospital-dashboard .hcc-section-ipd .hcc-section-kicker { color: #7c3aed; }
        #main-hospital-dashboard .hcc-section-ot .hcc-section-kicker { color: #db2777; }
        #main-hospital-dashboard .hcc-section-lab .hcc-section-kicker { color: #0891b2; }
        #main-hospital-dashboard .hcc-section-hr .hcc-section-kicker { color: #9333ea; }
        #main-hospital-dashboard .hcc-section-inventory .hcc-section-kicker { color: #ea580c; }
        #main-hospital-dashboard .hcc-section-sales .hcc-section-kicker { color: #16a34a; }
        #main-hospital-dashboard .hcc-section-finance .hcc-section-kicker { color: #d97706; }

        #main-hospital-dashboard .hcc-section-heading h2 {
            font-size: 21px;
            line-height: 1.2;
            margin: 3px 0 5px;
            color: var(--hcc-text);
            letter-spacing: -0.025em;
        }

        #main-hospital-dashboard .hcc-section-heading p {
            color: var(--hcc-muted);
            margin: 0;
            max-width: 760px;
        }

        #main-hospital-dashboard .hcc-mini-summary {
            display: flex;
            gap: 8px;
        }

        #main-hospital-dashboard .hcc-mini-summary span {
            border: 1px solid var(--hcc-border);
            background: var(--hcc-card);
            border-radius: 999px;
            padding: 5px 10px;
            color: var(--hcc-muted);
            font-size: 11px;
        }

        #main-hospital-dashboard .hcc-source-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
        }

        #main-hospital-dashboard .hcc-source-card {
            background: var(--hcc-card);
            border: 1px solid var(--hcc-border);
            border-left: 4px solid var(--hcc-blue);
            border-radius: 14px;
            padding: 15px;
            min-height: 185px;
            display: flex;
            flex-direction: column;
            box-shadow: 0 5px 16px rgba(15,23,42,.05);
        }

        #main-hospital-dashboard .hcc-source-card.hcc-status-green { border-left-color: var(--hcc-green); }
        #main-hospital-dashboard .hcc-source-card.hcc-status-amber { border-left-color: var(--hcc-amber); }
        #main-hospital-dashboard .hcc-source-card.hcc-status-red { border-left-color: var(--hcc-red); }
        #main-hospital-dashboard .hcc-source-card.hcc-status-grey { border-left-color: var(--hcc-grey); }

        #main-hospital-dashboard .hcc-source-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
        }

        #main-hospital-dashboard .hcc-source-name {
            color: var(--hcc-muted);
            text-transform: uppercase;
            letter-spacing: .06em;
            font-size: 10px;
            font-weight: 800;
        }

        #main-hospital-dashboard .hcc-source-card > strong {
            font-size: 22px;
            color: var(--hcc-text);
            margin: 10px 0 4px;
            letter-spacing: -.025em;
        }

        #main-hospital-dashboard .hcc-source-card p {
            color: var(--hcc-muted);
            margin: 0 0 7px;
            font-size: 11px;
        }

        #main-hospital-dashboard .hcc-source-card small {
            color: var(--hcc-text);
            font-weight: 650;
        }

        #main-hospital-dashboard .hcc-source-actions {
            margin-top: auto;
            padding-top: 10px;
            display: flex;
            gap: 14px;
        }

        #main-hospital-dashboard .hcc-boundary-note {
            margin-top: 12px;
            border: 1px dashed #94a3b8;
            border-radius: 12px;
            padding: 12px 14px;
            color: var(--hcc-muted);
            background: var(--hcc-card-soft);
        }

        #main-hospital-dashboard .hcc-metric-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
            gap: 10px;
            margin-bottom: 12px;
        }

        #main-hospital-dashboard .hcc-metric {
            position: relative;
            border: 1px solid var(--hcc-border);
            border-top: 3px solid var(--hcc-blue);
            background: var(--hcc-card);
            border-radius: 13px;
            padding: 13px 14px 12px;
            min-height: 118px;
            box-shadow: 0 4px 14px rgba(15, 23, 42, .045);
            overflow: hidden;
        }

        #main-hospital-dashboard .hcc-metric.hcc-status-green { border-top-color: var(--hcc-green); }
        #main-hospital-dashboard .hcc-metric.hcc-status-amber { border-top-color: var(--hcc-amber); }
        #main-hospital-dashboard .hcc-metric.hcc-status-red { border-top-color: var(--hcc-red); }
        #main-hospital-dashboard .hcc-metric.hcc-status-grey { border-top-color: var(--hcc-grey); }

        #main-hospital-dashboard .hcc-metric-top {
            display: flex;
            align-items: center;
            gap: 8px;
            min-height: 28px;
        }

        #main-hospital-dashboard .hcc-metric-icon {
            width: 27px;
            height: 27px;
            flex: 0 0 27px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            background: var(--hcc-card-soft);
            font-size: 14px;
        }

        #main-hospital-dashboard .hcc-metric-label {
            color: var(--hcc-muted);
            font-size: 10px;
            font-weight: 750;
            text-transform: uppercase;
            letter-spacing: .035em;
            line-height: 1.25;
        }

        #main-hospital-dashboard .hcc-metric strong {
            display: block;
            color: var(--hcc-text);
            font-size: 19px;
            margin-top: 9px;
            line-height: 1.15;
            overflow-wrap: anywhere;
        }

        #main-hospital-dashboard .hcc-metric-state {
            display: inline-flex;
            margin-top: 7px;
            font-size: 8px;
            font-weight: 800;
            letter-spacing: .08em;
            color: var(--hcc-muted);
        }

        #main-hospital-dashboard .hcc-chart-grid,
        #main-hospital-dashboard .hcc-grid-2 {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
            margin-bottom: 12px;
        }

        #main-hospital-dashboard .hcc-chart-card {
            min-width: 0;
        }

        #main-hospital-dashboard .hcc-chart {
            min-height: 280px;
            padding: 10px 8px 3px;
            overflow: hidden;
        }

        #main-hospital-dashboard .hcc-chart .frappe-chart,
        #main-hospital-dashboard .hcc-chart svg {
            max-width: 100%;
        }

        #main-hospital-dashboard .hcc-chart-empty,
        #main-hospital-dashboard .hcc-empty {
            min-height: 150px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            color: var(--hcc-muted);
            padding: 22px;
        }

        #main-hospital-dashboard .hcc-empty strong {
            color: var(--hcc-text);
            margin-bottom: 5px;
        }

        #main-hospital-dashboard .hcc-empty-success {
            min-height: 105px;
            background: #f0fdf4;
        }

        html[data-theme="dark"] #main-hospital-dashboard .hcc-empty-success,
        body[data-theme="dark"] #main-hospital-dashboard .hcc-empty-success {
            background: #153425;
        }

        #main-hospital-dashboard .hcc-table-card {
            margin-bottom: 12px;
        }

        #main-hospital-dashboard .hcc-table-card.hcc-nested {
            margin-bottom: 0;
        }

        #main-hospital-dashboard .hcc-table-wrap {
            overflow: auto;
            max-height: 430px;
        }

        #main-hospital-dashboard .hcc-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
        }

        #main-hospital-dashboard .hcc-table th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: var(--hcc-card-soft);
            color: var(--hcc-muted);
            text-transform: uppercase;
            letter-spacing: .045em;
            font-size: 9px;
            text-align: left;
            padding: 9px 11px;
            border-bottom: 1px solid var(--hcc-border);
            white-space: nowrap;
        }

        #main-hospital-dashboard .hcc-table td {
            padding: 9px 11px;
            border-bottom: 1px solid var(--hcc-border);
            color: var(--hcc-text);
            white-space: nowrap;
        }

        #main-hospital-dashboard .hcc-table tbody tr:hover {
            background: var(--hcc-card-soft);
        }

        #main-hospital-dashboard .hcc-table tbody tr:last-child td { border-bottom: 0; }
        #main-hospital-dashboard .hcc-table-empty { text-align: center; color: var(--hcc-muted) !important; }

        #main-hospital-dashboard .hcc-footer-note {
            border: 1px dashed var(--hcc-border);
            border-radius: 12px;
            padding: 11px 13px;
            color: var(--hcc-muted);
            background: var(--hcc-card-soft);
            text-align: center;
            font-size: 10px;
        }

        @media (max-width: 980px) {
            #main-hospital-dashboard .hcc-hero { grid-template-columns: 1fr; }
            #main-hospital-dashboard .hcc-chart-grid,
            #main-hospital-dashboard .hcc-grid-2 { grid-template-columns: 1fr; }
            #main-hospital-dashboard .hcc-alert-row { grid-template-columns: 58px minmax(0, 1fr) auto; }
            #main-hospital-dashboard .hcc-alert-domain { display: none; }
        }

        @media (max-width: 640px) {
            #main-hospital-dashboard .hcc-hero { padding: 20px; border-radius: 15px; }
            #main-hospital-dashboard .hcc-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            #main-hospital-dashboard .hcc-kpi { min-height: 104px; padding: 13px; }
            #main-hospital-dashboard .hcc-kpi-value { font-size: 18px; }
            #main-hospital-dashboard .hcc-section-heading,
            #main-hospital-dashboard .hcc-strip-heading { flex-direction: column; align-items: flex-start; }
            #main-hospital-dashboard .hcc-strip-note { text-align: left; }
            #main-hospital-dashboard .hcc-section { padding: 13px; border-radius: 15px; }
            #main-hospital-dashboard .hcc-alert-row { grid-template-columns: 52px minmax(0, 1fr); }
            #main-hospital-dashboard .hcc-alert-row .hcc-link-button { grid-column: 2; justify-self: start; }
            #main-hospital-dashboard .hcc-source-grid { grid-template-columns: 1fr; }
        }

        @media print {
            .page-head,
            .page-form,
            .hcc-tabs,
            #main-hospital-dashboard button,
            .datatable { display: none !important; }

            #main-hospital-dashboard .hcc-section { display: block !important; page-break-inside: avoid; margin-top: 18px; }
            #main-hospital-dashboard .hcc-chart-grid,
            #main-hospital-dashboard .hcc-grid-2 { grid-template-columns: 1fr 1fr; }
            #main-hospital-dashboard .hcc-hero,
            #main-hospital-dashboard .hcc-panel,
            #main-hospital-dashboard .hcc-kpi,
            #main-hospital-dashboard .hcc-source-card { box-shadow: none; }
        }
    `;

    document.head.appendChild(style);
}
