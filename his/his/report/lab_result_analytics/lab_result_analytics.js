// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

/* eslint-disable */

// Lab Result Analytics — Blood. Everything client-side in one file.
//
// ROW GRAIN: one row per ANALYTE (child table row), joined to its parent
// Lab Result document. That means:
//
//   * Test volume, abnormal counts, top tests -> count rows directly
//   * Document counts (status/department/section/technician mix)
//     -> de-duplicate by `name` first
//   * Normal vs Abnormal by Doctor / Department / Gender / Age
//     -> count rows directly (a normal/abnormal verdict belongs to the
//        analyte, not the document), grouped by a parent-level dimension
//
// A 12-analyte panel is 12 rows but ONE document. Counting a document-level
// mix per row would weight that panel twelve times. `tally_documents` below
// de-duplicates; `tally` and `tally_normal_abnormal` do not, by design.
//
// The Python side aliases every resolved field to a stable role name
// (patient, test_name, result_value, result_flag, patient_sex, patient_age,
// patient_age_type, age_group, ...), so nothing here changes when your
// schema differs. A chart whose role never resolved renders nothing instead
// of breaking.

frappe.query_reports["Lab Result Analytics"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -6),
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
			fieldname: "range",
			label: __("Group Chart By"),
			fieldtype: "Select",
			options: ["Daily", "Weekly", "Monthly", "Quarterly", "Yearly"],
			default: "Monthly",
		},
		{
			// Matches against the child table's test name, partial match.
			fieldname: "test_name",
			label: __("Test Name"),
			fieldtype: "Data",
		},
		{
			fieldname: "department",
			label: __("Medical Department"),
			fieldtype: "Link",
			options: "Medical Department",
		},
		{
			fieldname: "practitioner",
			label: __("Doctor"),
			fieldtype: "Link",
			options: "Healthcare Practitioner",
		},
		{
			fieldname: "patient_sex",
			label: __("Gender"),
			fieldtype: "Data",
		},
		{
			// Data, not Select — run inspect() to see your real status values,
			// then switch to Select + options if you want a dropdown.
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Data",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "result_flag") {
			const palette = {
				[__("Normal")]: "green",
				[__("High")]: "red",
				[__("Low")]: "orange",
			};
			const colour = palette[data.result_flag];
			value = colour
				? `<span class="indicator-pill ${colour}">${data.result_flag}</span>`
				: `<span style="color: var(--text-muted);">${data.result_flag}</span>`;
		}

		// An out-of-range value is what a clinician scans for.
		if (column.fieldname === "result_value") {
			if (data.result_flag === __("High") || data.result_flag === __("Low")) {
				const arrow = data.result_flag === __("High") ? "&#9650;" : "&#9660;";
				const colour = data.result_flag === __("High") ? "#e24b4a" : "#ff8c00";
				value = `<span style="color:${colour};font-weight:600;">${value} ${arrow}</span>`;
			}
		}

		if (column.fieldname === "status" && data.status) {
			const palette = {
				Approved: "green",
				Verified: "green",
				Released: "green",
				Completed: "blue",
				"In Process": "blue",
				Draft: "orange",
				Pending: "orange",
				Rejected: "red",
				Cancelled: "grey",
			};
			const colour = palette[data.status];
			if (colour) {
				value = `<span class="indicator-pill ${colour}">${data.status}</span>`;
			}
		}

		return value;
	},

	onload: function (report) {
		wrapper.querySelector("#ipd-extra-chart-section")?.remove();
	wrapper.querySelector("#opd-extra-pie-section")?.remove();
		report.page.add_inner_button(__("Refresh Charts"), () => render_lab_charts());
		report.page.add_inner_button(__("Toggle Charts"), () => {
			const $c = report.page.main.find(".lab-extra-charts");
			$c.length && $c.toggle();
		});
	},

	after_datatable_render: function () {
		render_lab_charts();
	},
};

// ---------------------------------------------------------------------------
// Extra charts
//
// A Script Report renders exactly ONE built-in chart (Normal vs Abnormal per
// period, returned from Python — Daily / Weekly / Monthly / Quarterly /
// Yearly comparison, driven by the "Group Chart By" filter). Everything
// below is drawn client-side from rows already in memory — no extra server
// round-trip.
// ---------------------------------------------------------------------------

function render_lab_charts() {
	const report = frappe.query_report;
	if (!report || !report.data || !report.data.length) return;

	const $container = get_chart_container(report);
	if (!$container) return;

	$container.empty();
	const rows = report.data;

	// --- Analyte-level: count rows -----------------------------------------

	draw($container, {
		id: "lab-result-mix",
		title: __("Result Distribution"),
		type: "donut",
		colors: ["#28a745", "#e24b4a", "#ff8c00", "#98a1a9"],
		...tally(rows, "result_flag", 4),
	});

	draw($container, {
		id: "lab-top-tests",
		title: __("Most Ordered Tests"),
		type: "bar",
		colors: ["#00a3ac"],
		...tally(rows, "test_name", 10),
	});

	draw_most_abnormal($container, rows);

	// --- Normal vs Abnormal, grouped by a parent-level dimension -----------

	draw_grouped($container, {
		id: "lab-by-doctor",
		title: __("Normal vs Abnormal by Doctor"),
		...tally_normal_abnormal(rows, "practitioner", 10),
	});

	draw_grouped($container, {
		id: "lab-by-department",
		title: __("Normal vs Abnormal by Department"),
		...tally_normal_abnormal(rows, "department", 10),
	});

	draw_grouped($container, {
		id: "lab-by-gender",
		title: __("Normal vs Abnormal by Gender"),
		...tally_normal_abnormal(rows, "patient_sex", 3),
	});

	draw_grouped($container, {
		id: "lab-by-age",
		title: __("Normal vs Abnormal by Age Group"),
		...tally_normal_abnormal(rows, "age_group", 12, sort_age_group),
	});

	// --- Document-level: de-duplicate by parent ----------------------------

	draw($container, {
		id: "lab-status-mix",
		title: __("Status Mix (documents)"),
		type: "donut",
		colors: ["#28a745", "#5e64ff", "#ffa00a", "#e24b4a", "#98a1a9"],
		...tally_documents(rows, "status", 6),
	});

	draw($container, {
		id: "lab-volume-by-department",
		title: __("Volume by Department (documents)"),
		type: "bar",
		colors: ["#5e64ff"],
		...tally_documents(rows, "department", 8),
	});

	draw($container, {
		id: "lab-by-section",
		title: __("Volume by Section (documents)"),
		type: "pie",
		colors: ["#5e64ff", "#00a3ac", "#743ee2", "#ffa00a", "#28a745", "#e24b4a"],
		...tally_documents(rows, "section", 6),
	});

	draw($container, {
		id: "lab-technician-load",
		title: __("Workload by Technician (documents)"),
		type: "bar",
		colors: ["#743ee2"],
		...tally_documents(rows, "technician", 8),
	});

	draw($container, {
		id: "lab-gender-split",
		title: __("Patient Gender Split (documents)"),
		type: "pie",
		colors: ["#5e64ff", "#e24b4a", "#ffa00a"],
		...tally_documents(rows, "patient_sex", 3),
	});

	draw($container, {
		id: "lab-age-split",
		title: __("Patient Age Group Split (documents)"),
		type: "pie",
		colors: ["#5e64ff", "#00a3ac", "#743ee2", "#ffa00a", "#28a745", "#e24b4a", "#98a1a9"],
		...tally_documents(rows, "age_group", 10, sort_age_group),
	});

	draw_workload_heatmap($container, rows);
}

// Which blood tests come back abnormal most often. Rate, not raw count — a
// test ordered 2000 times will always top a raw abnormal count, which tells
// you nothing. Minimum 10 results so a 1-of-1 test cannot show 100%.
function draw_most_abnormal($container, rows) {
	const stats = {};

	rows.forEach((r) => {
		if (!r.test_name) return;
		if (r.result_flag === __("Not Evaluated")) return;

		if (!stats[r.test_name]) stats[r.test_name] = { total: 0, abnormal: 0 };
		stats[r.test_name].total += 1;
		if (r.result_flag === __("High") || r.result_flag === __("Low")) {
			stats[r.test_name].abnormal += 1;
		}
	});

	const sorted = Object.entries(stats)
		.filter(([, v]) => v.total >= 10)
		.map(([k, v]) => [k, (v.abnormal / v.total) * 100])
		.filter(([, pct]) => pct > 0)
		.sort((a, b) => b[1] - a[1])
		.slice(0, 10);

	if (!sorted.length) return;

	draw($container, {
		id: "lab-most-abnormal",
		title: __("Most Frequently Abnormal (%)"),
		type: "bar",
		colors: ["#e24b4a"],
		labels: sorted.map((e) => e[0]),
		values: sorted.map((e) => Math.round(e[1] * 10) / 10),
	});
}

// Daily workload heatmap, counted over documents.
function draw_workload_heatmap($container, rows) {
	const points = {};
	const seen = new Set();
	let min = null;
	let max = null;

	rows.forEach((r) => {
		if (!r.ordered_at || seen.has(r.name)) return;
		seen.add(r.name);

		const day = String(r.ordered_at).split(" ")[0].split("T")[0];
		const stamp = Math.floor(new Date(day + "T00:00:00").getTime() / 1000);
		if (!stamp || isNaN(stamp)) return;

		points[stamp] = (points[stamp] || 0) + 1;
		if (min === null || stamp < min) min = stamp;
		if (max === null || stamp > max) max = stamp;
	});

	if (min === null) return;

	const $card = $(`
		<div class="frappe-card" style="padding:12px;grid-column:1 / -1;">
			<div style="font-weight:600;font-size:13px;margin-bottom:4px;">${__("Daily Workload")}</div>
			<div id="lab-heatmap"></div>
		</div>
	`);
	$container.append($card);

	// The heatmap data shape is the one frappe-charts option that varies most
	// between versions, so failure removes the card rather than the page.
	try {
		new frappe.Chart("#lab-heatmap", {
			data: { dataPoints: points, start: new Date(min * 1000), end: new Date(max * 1000) },
			type: "heatmap",
			height: 150,
			countLabel: __("Results"),
			discreteDomains: 0,
			colors: ["#ebedf0", "#f9d5d3", "#f0a9a5", "#e2716c", "#c73832"],
		});
	} catch (e) {
		$card.remove();
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function get_chart_container(report) {
	const $main = report.page && report.page.main;
	if (!$main) return null;

	let $container = $main.find(".lab-extra-charts");
	if ($container.length) return $container;

	$container = $(
		'<div class="lab-extra-charts" style="display:grid;' +
			"grid-template-columns:repeat(auto-fit,minmax(320px,1fr));" +
			'gap:12px;margin:12px 0;"></div>'
	);

	const $anchor = $main.find(".report-wrapper").first();
	if ($anchor.length) {
		$container.insertBefore($anchor);
	} else {
		$container.prependTo($main);
	}

	return $container;
}

function draw($container, opts) {
	if (!opts.labels || !opts.labels.length) return;

	const $card = $(`
		<div class="frappe-card" style="padding:12px;">
			<div style="font-weight:600;font-size:13px;margin-bottom:4px;">${opts.title}</div>
			<div id="${opts.id}"></div>
		</div>
	`);
	$container.append($card);

	new frappe.Chart(`#${opts.id}`, {
		data: { labels: opts.labels, datasets: [{ name: opts.title, values: opts.values }] },
		// frappe-charts types: bar, line, pie, donut, percentage, heatmap, axis-mixed
		type: opts.type,
		height: 240,
		colors: opts.colors,
		maxSlices: 6,
		axisOptions: { shortenYAxisNumbers: 1 },
		barOptions: { spaceRatio: 0.4 },
	});
}

// Grouped bar variant of draw() for two-dataset (Normal / Abnormal) charts.
function draw_grouped($container, opts) {
	if (!opts.labels || !opts.labels.length) return;

	const $card = $(`
		<div class="frappe-card" style="padding:12px;">
			<div style="font-weight:600;font-size:13px;margin-bottom:4px;">${opts.title}</div>
			<div id="${opts.id}"></div>
		</div>
	`);
	$container.append($card);

	new frappe.Chart(`#${opts.id}`, {
		data: { labels: opts.labels, datasets: opts.datasets },
		type: "bar",
		height: 240,
		colors: ["#28a745", "#e24b4a"],
		maxSlices: 12,
		axisOptions: { shortenYAxisNumbers: 1 },
		barOptions: { spaceRatio: 0.4 },
	});
}

// Count ANALYTE rows by field. Use for test-level questions.
function tally(rows, fieldname, limit) {
	return top_n(count_by(rows, fieldname, null), limit);
}

// Count DOCUMENTS by field — each Lab Result counted once however many
// analytes it holds. Use for document-level questions.
function tally_documents(rows, fieldname, limit, sorter) {
	return top_n(count_by(rows, fieldname, new Set()), limit, sorter);
}

// Normal vs Abnormal analyte counts, grouped by a parent-level dimension
// (practitioner / department / patient_sex / age_group). Rows with no
// verdict ("Not Evaluated") are excluded — they are neither normal nor
// abnormal and would understate both bars.
function tally_normal_abnormal(rows, fieldname, limit, sorter) {
	const buckets = {};
	let populated = false;

	rows.forEach((r) => {
		if (r.result_flag === __("Not Evaluated")) return;

		const raw = r[fieldname];
		if (raw !== null && raw !== undefined && raw !== "") populated = true;
		const key = raw || __("Not Set");

		if (!buckets[key]) buckets[key] = { normal: 0, abnormal: 0 };
		if (r.result_flag === __("Normal")) {
			buckets[key].normal += 1;
		} else {
			buckets[key].abnormal += 1;
		}
	});

	if (!populated) return { labels: [], datasets: [] };

	let keys = Object.keys(buckets);
	keys = sorter
		? keys.sort(sorter)
		: keys.sort((a, b) => buckets[b].normal + buckets[b].abnormal - (buckets[a].normal + buckets[a].abnormal));
	keys = keys.slice(0, limit);

	return {
		labels: keys,
		datasets: [
			{ name: __("Normal"), values: keys.map((k) => buckets[k].normal) },
			{ name: __("Abnormal"), values: keys.map((k) => buckets[k].abnormal) },
		],
	};
}

// Sorts age-group labels ("25 Year", "6 Month", "10 Day", or a bare number)
// by unit (Day < Month < Year) then by numeric value, so age charts read
// left-to-right instead of alphabetically ("10 Year" before "2 Year").
const AGE_UNIT_ORDER = { day: 0, month: 1, year: 2 };

function parse_age_group(label) {
	const match = String(label || "").match(/^(-?\d+(?:\.\d+)?)\s*(\w+)?/);
	if (!match) return { unit: 99, value: 0 };
	const unit = (match[2] || "").toLowerCase();
	return {
		value: parseFloat(match[1]),
		unit: unit in AGE_UNIT_ORDER ? AGE_UNIT_ORDER[unit] : 99,
	};
}

function sort_age_group(a, b) {
	if (a === __("Not Set")) return 1;
	if (b === __("Not Set")) return -1;
	const pa = parse_age_group(a);
	const pb = parse_age_group(b);
	return pa.unit - pb.unit || pa.value - pb.value;
}

function count_by(rows, fieldname, seen) {
	const counts = {};
	let populated = false;

	rows.forEach((r) => {
		if (seen) {
			if (seen.has(r.name)) return;
			seen.add(r.name);
		}
		const raw = r[fieldname];
		if (raw !== null && raw !== undefined && raw !== "") populated = true;
		const key = raw || __("Not Set");
		counts[key] = (counts[key] || 0) + 1;
	});

	// A role that never resolved yields no chart rather than a "Not Set" pie.
	return populated ? counts : null;
}

function top_n(counts, limit, sorter) {
	if (!counts) return { labels: [], values: [] };

	let keys = Object.keys(counts);
	keys = sorter ? keys.sort(sorter) : keys.sort((a, b) => counts[b] - counts[a]);
	keys = keys.slice(0, limit);

	return { labels: keys, values: keys.map((k) => counts[k]) };
}