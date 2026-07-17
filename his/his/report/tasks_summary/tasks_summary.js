// Copyright (c) 2026, Rasiin Tech and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Tasks Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		},
		{
			fieldname: "assigned_to",
			label: __("Assigned To"),
			fieldtype: "Link",
			options: "User",
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		},
		{
			fieldname: "priority",
			label: __("Priority"),
			fieldtype: "Select",
			options: ["", "Low", "Medium", "High", "Urgent"],
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Open", "Working", "Pending Review", "Overdue", "Completed"],
			on_change: function(report) {
				reload_tasks_report_ui(report);
			}
		}
	],

	onload: function(report) {
		enhance_tasks_summary_cards(report);
	},

	refresh: function(report) {
		enhance_tasks_summary_cards(report);
	},

	after_datatable_render: function(report) {
		if (!is_tasks_summary_report(report)) return;
		enhance_tasks_summary_cards(report);
	},

	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (!data) return value;

		if (column.fieldname === "delay") {
			if (data.delay > 0) {
				value = `<span style="color:#dc2626; font-weight:700;">${value}</span>`;
			} else {
				value = `<span style="color:#16a34a; font-weight:700;">${value}</span>`;
			}
		}

		if (column.fieldname === "status") {
			const status_colors = {
				"Open": "#0ea5e9",
				"Working": "#2563eb",
				"Pending Review": "#d97706",
				"Overdue": "#dc2626",
				"Completed": "#16a34a"
			};

			if (status_colors[data.status]) {
				value = `<span style="color:${status_colors[data.status]}; font-weight:600;">${value}</span>`;
			}
		}

		if (column.fieldname === "priority") {
			const priority_colors = {
				"Low": "#64748b",
				"Medium": "#0ea5e9",
				"High": "#ea580c",
				"Urgent": "#dc2626"
			};

			if (priority_colors[data.priority]) {
				value = `<span style="color:${priority_colors[data.priority]}; font-weight:600;">${value}</span>`;
			}
		}

		return value;
	}
};

function is_tasks_summary_report(report) {
	return report && report.report_name === "Tasks Summary";
}

function get_tasks_wrapper(report) {
	if (report && report.page && report.page.wrapper && report.page.wrapper[0]) {
		return report.page.wrapper[0];
	}
	return null;
}

function reload_tasks_report_ui(report) {
	if (!report) return;

	report.refresh();

	setTimeout(() => {
		if (!is_tasks_summary_report(report)) return;
		enhance_tasks_summary_cards(report);
	}, 500);
}

function enhance_tasks_summary_cards(report) {
	if (!is_tasks_summary_report(report)) return;

	add_tasks_summary_css();

	const try_apply = () => {
		if (!is_tasks_summary_report(report)) return;

		const wrapper = get_tasks_wrapper(report);
		if (!wrapper) return;

		const containers = wrapper.querySelectorAll(`
			.report-summary,
			.summary-wrapper
		`);

		containers.forEach(container => {
			container.style.display = "flex";
			container.style.flexWrap = "wrap";
			container.style.gap = "16px";
			container.style.background = "transparent";
			container.style.padding = "12px 0";
			container.style.border = "none";
			container.style.boxShadow = "none";
		});

		const cards = wrapper.querySelectorAll(`
			.report-summary .summary-item,
			.report-summary .report-summary-item,
			.report-summary .card,
			.report-summary > div,
			.summary-wrapper .summary-item,
			.summary-wrapper .report-summary-item,
			.summary-wrapper .card,
			.summary-wrapper > div,
			.query-report .summary-item,
			.query-report .report-summary-item
		`);

		cards.forEach((card, index) => {
			const labelEl = card.querySelector(".summary-label, .label");
			const valueEl = card.querySelector(".summary-value, .value");

			if (!labelEl || !valueEl) return;

			const rawLabel = (labelEl.textContent || "").trim();
			const config = get_task_card_config(rawLabel, index);

			card.style.background = config.background;
			card.style.border = "1px solid #dbe4ee";
			card.style.borderTop = `4px solid ${config.color}`;
			card.style.borderRadius = "16px";
			card.style.boxShadow = "0 6px 18px rgba(0,0,0,0.08)";
			card.style.padding = "18px 22px";
			card.style.minWidth = "180px";
			card.style.minHeight = "130px";
			card.style.flex = "1 1 180px";
			card.style.margin = "0";
			card.style.position = "relative";
			card.style.overflow = "hidden";
			card.style.cursor = "pointer";
			card.style.transition = "all 0.25s ease";

			labelEl.style.fontSize = "14px";
			labelEl.style.fontWeight = "600";
			labelEl.style.color = "#64748b";
			labelEl.style.marginBottom = "10px";
			labelEl.style.display = "flex";
			labelEl.style.alignItems = "center";
			labelEl.style.gap = "8px";

			valueEl.style.fontSize = "30px";
			valueEl.style.fontWeight = "700";
			valueEl.style.color = config.color;

			add_task_icon_to_label(labelEl, config.icon);
			animate_task_value(valueEl);

			card.onmouseenter = () => {
				card.style.transform = "translateY(-4px)";
				card.style.boxShadow = "0 12px 24px rgba(0,0,0,0.12)";
			};

			card.onmouseleave = () => {
				if (!card.classList.contains("summary-card-active")) {
					card.style.transform = "translateY(0)";
					card.style.boxShadow = "0 6px 18px rgba(0,0,0,0.08)";
				}
			};

			card.onclick = () => {
				wrapper.querySelectorAll(".summary-card-active").forEach(el => {
					el.classList.remove("summary-card-active");
					el.style.transform = "translateY(0)";
					el.style.boxShadow = "0 6px 18px rgba(0,0,0,0.08)";
				});

				card.classList.add("summary-card-active");
				card.style.transform = "translateY(-4px)";
				card.style.boxShadow = `0 14px 30px ${hexToRgbaTask(config.color, 0.25)}`;
			};
		});
	};

	setTimeout(try_apply, 300);
	setTimeout(try_apply, 800);
	setTimeout(try_apply, 1400);
}

function get_task_card_config(label, index) {
	const text = (label || "").toLowerCase();

	if (text.includes("total")) {
		return {
			icon: "📊",
			color: "#0f766e",
			background: "linear-gradient(180deg, #ffffff 0%, #f0fdfa 100%)"
		};
	}

	if (text.includes("on track")) {
		return {
			icon: "✅",
			color: "#2563eb",
			background: "linear-gradient(180deg, #ffffff 0%, #eff6ff 100%)"
		};
	}

	if (text.includes("delayed")) {
		return {
			icon: "⏰",
			color: "#dc2626",
			background: "linear-gradient(180deg, #ffffff 0%, #fef2f2 100%)"
		};
	}

	if (text.includes("completed")) {
		return {
			icon: "✔️",
			color: "#7c3aed",
			background: "linear-gradient(180deg, #ffffff 0%, #f5f3ff 100%)"
		};
	}

	if (text.includes("department")) {
		return {
			icon: "🏢",
			color: "#d97706",
			background: "linear-gradient(180deg, #ffffff 0%, #fff7ed 100%)"
		};
	}

	const fallback = [
		{ icon: "📈", color: "#2563eb", background: "linear-gradient(180deg, #ffffff 0%, #eff6ff 100%)" },
		{ icon: "📌", color: "#d97706", background: "linear-gradient(180deg, #ffffff 0%, #fff7ed 100%)" },
		{ icon: "📋", color: "#7c3aed", background: "linear-gradient(180deg, #ffffff 0%, #f5f3ff 100%)" }
	];

	return fallback[index % fallback.length];
}

function add_task_icon_to_label(labelEl, icon) {
	if (!labelEl || labelEl.querySelector(".summary-card-icon")) return;

	const iconSpan = document.createElement("span");
	iconSpan.className = "summary-card-icon";
	iconSpan.textContent = icon;
	iconSpan.style.fontSize = "16px";
	iconSpan.style.display = "inline-flex";
	iconSpan.style.alignItems = "center";
	iconSpan.style.justifyContent = "center";

	labelEl.prepend(iconSpan);
}

function animate_task_value(valueEl) {
	if (!valueEl || valueEl.dataset.animated === "1") return;

	const rawText = (valueEl.textContent || "").replace(/,/g, "").trim();
	const finalValue = parseInt(rawText, 10);

	if (isNaN(finalValue)) return;

	valueEl.dataset.animated = "1";
	let current = 0;
	const duration = 700;
	const stepTime = 20;
	const increment = Math.max(1, Math.ceil(finalValue / (duration / stepTime)));

	const timer = setInterval(() => {
		current += increment;

		if (current >= finalValue) {
			current = finalValue;
			clearInterval(timer);
		}

		valueEl.textContent = current.toLocaleString();
	}, stepTime);
}

function hexToRgbaTask(hex, alpha) {
	const sanitized = hex.replace("#", "");
	const bigint = parseInt(sanitized, 16);
	const r = (bigint >> 16) & 255;
	const g = (bigint >> 8) & 255;
	const b = bigint & 255;
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function add_tasks_summary_css() {
	if (document.getElementById("tasks-summary-cards-css")) return;

	const style = document.createElement("style");
	style.id = "tasks-summary-cards-css";
	style.innerHTML = `
		.report-summary,
		.summary-wrapper {
			background: transparent !important;
			border: none !important;
			box-shadow: none !important;
		}

		.report-summary .summary-item,
		.report-summary .report-summary-item,
		.report-summary .card,
		.report-summary > div,
		.summary-wrapper .summary-item,
		.summary-wrapper .report-summary-item,
		.summary-wrapper .card,
		.summary-wrapper > div,
		.query-report .summary-item,
		.query-report .report-summary-item {
			border-radius: 16px !important;
			min-height: 130px !important;
			padding: 18px 22px !important;
			transition: all 0.25s ease !important;
		}

		.report-summary .summary-label,
		.report-summary .label,
		.summary-wrapper .summary-label,
		.summary-wrapper .label {
			font-size: 14px !important;
			font-weight: 600 !important;
			line-height: 1.4 !important;
		}

		.report-summary .summary-value,
		.report-summary .value,
		.summary-wrapper .summary-value,
		.summary-wrapper .value {
			font-size: 30px !important;
			font-weight: 700 !important;
			line-height: 1.1 !important;
		}

		.summary-card-active {
			outline: 2px solid rgba(37, 99, 235, 0.12);
		}
	`;
	document.head.appendChild(style);
}