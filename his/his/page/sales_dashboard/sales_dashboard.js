frappe.pages["sales-dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Sales & Payments Dashboard",
		single_column: true,
	});

	// Load Chart.js
	if (typeof Chart !== "undefined") {
		sales_dashboard.init(wrapper);
	} else {
		frappe.require(["assets/frappe/js/lib/chart.min.js"], function () {
			sales_dashboard.init(wrapper);
		});
	}
};

var sales_dashboard = {
	charts: {},
	data: {},
	app_name: "his.his.page.sales_dashboard.sales_dashboard", // ← CHANGE THIS TO YOUR APP NAME

	init: function (wrapper) {
		$(wrapper).find(".layout-main-section").html(
			frappe.render_template("sales_dashboard")
		);
		this.set_defaults();
		this.load_filter_options();
		this.refresh();

		// Bind refresh button
		$("#sd-refresh-btn").on("click", () => this.refresh());
		$("#sd-from-date, #sd-to-date, #sd-customer, #sd-owner, #sd-cost-center").on(
			"change",
			() => this.refresh()
		);
	},

	set_defaults: function () {
		var today = frappe.datetime.get_today();
		var first = frappe.datetime.month_start();
		$("#sd-from-date").val(first);
		$("#sd-to-date").val(today);
		$("#sd-date-range").text(first + " to " + today);
	},

	get_filters: function () {
		return {
			from_date: $("#sd-from-date").val(),
			to_date: $("#sd-to-date").val(),
			customer: $("#sd-customer").val(),
			owner: $("#sd-owner").val(),
			cost_center: $("#sd-cost-center").val(),
		};
	},

	load_filter_options: function () {
		frappe.call({
			method: this.app_name + ".get_filter_options",
			callback: (r) => {
				if (!r.message) return;
				// Load customers
				r.message.customers.forEach((c) => {
					$("#sd-customer").append(`<option value="${c}">${c}</option>`);
				});
				// Load users (owners)
				r.message.users.forEach((u) => {
					$("#sd-owner").append(`<option value="${u}">${u}</option>`);
				});
				// Load cost centers
				r.message.cost_centers.forEach((cc) => {
					$("#sd-cost-center").append(`<option value="${cc}">${cc}</option>`);
				});
			},
		});
	},

	refresh: function () {
		var f = this.get_filters();
		$("#sd-date-range").text(f.from_date + " to " + f.to_date);
		$("#sd-refresh-btn").prop("disabled", true).text("Loading...");

		frappe.call({
			method: this.app_name + ".get_dashboard_data",
			args: {
				from_date: f.from_date,
				to_date: f.to_date,
				customer: f.customer,
				owner: f.owner,
				cost_center: f.cost_center,
			},
			callback: (r) => {
				if (!r.message) {
					frappe.msgprint("Error loading dashboard data");
					return;
				}
				this.data = r.message;
				this.render_kpis();
				this.render_top_customers();
				this.render_by_customer_group();
				this.render_by_item_group();
				this.render_by_date();
				this.render_customer_table();
				this.render_item_group_table();

				$("#sd-refresh-btn").prop("disabled", false).html(
					'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Refresh'
				);
			},
		});
	},

	fmt_currency: function (v) {
		// Use simple currency formatting without HTML tags
		var num = parseFloat(v || 0);
		return new Intl.NumberFormat('en-US', {
			style: 'currency',
			currency: 'USD',
			minimumFractionDigits: 2,
			maximumFractionDigits: 2
		}).format(num);
	},

	// ── KPIs ──────────────────────────────────────────────────────────────────
	render_kpis: function () {
		var k = this.data.kpis || {};
		// Format with proper currency - use textContent to avoid HTML parsing
		document.getElementById("kpi-total-val").textContent = this.fmt_currency(k.total_sales || 0);
		document.getElementById("kpi-paid-val").textContent = this.fmt_currency(k.total_paid || 0);
		document.getElementById("kpi-unpaid-val").textContent = this.fmt_currency(k.total_unpaid || 0);
		document.getElementById("kpi-partial-val").textContent = this.fmt_currency(k.total_partial || 0);
		document.getElementById("kpi-orders-val").textContent = (k.total_orders || 0).toString();
		document.getElementById("kpi-customers-val").textContent = (k.total_customers || 0).toString();
		document.getElementById("kpi-payment-val").textContent = this.fmt_currency(k.payment_received || 0);
	},

	// ── Top Customers ─────────────────────────────────────────────────────────
	render_top_customers: function () {
		var rows = this.data.top_customers || [];
		if (rows.length === 0) {
			$("#sd-top-customers").html('<p class="sd-empty">No data</p>');
			return;
		}
		var max = rows[0].total || 1;
		var html = rows
			.map((item, i) => {
				var pct = Math.round((item.total / max) * 100);
				return `
				<div class="sd-bar-h">
					<span class="sd-rank">${i + 1}</span>
					<span class="sd-bar-label" title="${item.customer}">${item.customer}</span>
					<div class="sd-bar-track">
						<div class="sd-bar-fill" style="width:${pct}%;background:#3b6cf7"></div>
					</div>
					<span class="sd-bar-val">${this.fmt_currency(item.total)}</span>
				</div>`;
			})
			.join("");
		$("#sd-top-customers").html(html);
	},

	// ── Income by Customer Group ───────────────────────────────────────────────
	render_by_customer_group: function () {
		var rows = this.data.by_customer_group || [];
		if (rows.length === 0) {
			$("#sd-leg-cg").html('<p class="sd-empty">No data</p>');
			return;
		}
		var labels = rows.map((r) => r.group);
		var data = rows.map((r) => r.amount);
		var colors = ["#3b6cf7", "#22c55e", "#f59e0b", "#ec4899", "#14b8a6", "#6366f1"];
		var total = data.reduce((a, b) => a + b, 0);

		this._render_donut("sd-chart-cg", labels, data, colors);

		var legHtml = labels
			.map((l, i) => {
				var pct = total ? Math.round((data[i] / total) * 100) : 0;
				return `<span class="sd-leg-item"><span class="sd-leg-sq" style="background:${colors[i % colors.length]}"></span>${l} ${pct}%</span>`;
			})
			.join("");
		$("#sd-leg-cg").html(legHtml);
	},

	// ── Income by Item Group ───────────────────────────────────────────────────
	render_by_item_group: function () {
		var rows = this.data.by_item_group || [];
		if (rows.length === 0) {
			$("#sd-leg-ig").html('<p class="sd-empty">No data</p>');
			return;
		}
		var labels = rows.map((r) => r.group);
		var data = rows.map((r) => r.amount);
		var colors = ["#6366f1", "#14b8a6", "#f59e0b", "#ec4899", "#3b6cf7", "#22c55e"];
		var total = data.reduce((a, b) => a + b, 0);

		this._render_donut("sd-chart-ig", labels, data, colors);

		var legHtml = labels
			.map((l, i) => {
				var pct = total ? Math.round((data[i] / total) * 100) : 0;
				return `<span class="sd-leg-item"><span class="sd-leg-sq" style="background:${colors[i % colors.length]}"></span>${l} ${pct}%</span>`;
			})
			.join("");
		$("#sd-leg-ig").html(legHtml);
	},

	// ── Income by Date ─────────────────────────────────────────────────────────
	render_by_date: function () {
		var rows = this.data.by_date || [];
		if (rows.length === 0) return;

		var labels = rows.map((r) => r.date);
		var sales = rows.map((r) => r.sales || 0);
		var paid = rows.map((r) => r.paid || 0);
		var outstanding = rows.map((r) => r.outstanding || 0);

		if (this.charts.date) this.charts.date.destroy();
		this.charts.date = new Chart(document.getElementById("sd-chart-date"), {
			type: "bar",
			data: {
				labels: labels,
				datasets: [
					{
						label: "Sales",
						data: sales,
						backgroundColor: "#3b6cf7",
						borderWidth: 0,
					},
					{
						label: "Payments received",
						data: paid,
						backgroundColor: "#22c55e",
						borderWidth: 0,
					},
					{
						label: "Outstanding",
						data: outstanding,
						backgroundColor: "#f59e0b",
						borderWidth: 0,
					},
				],
			},
			options: {
				indexAxis: "y", // Makes it horizontal
				responsive: true,
				maintainAspectRatio: false,
				plugins: { legend: { display: false } },
				scales: {
					x: {
						grid: { color: "#f0f0f0" },
						ticks: {
							font: { size: 9 },
							callback: (v) => {
								if (v >= 1000000) return "$" + (v / 1000000).toFixed(1) + "M";
								if (v >= 1000) return "$" + (v / 1000).toFixed(0) + "k";
								return "$" + v;
							},
						},
					},
					y: {
						grid: { display: false },
						ticks: { font: { size: 9 } },
					},
				},
			},
		});
	},

	// ── Customer Group Table ───────────────────────────────────────────────────
	render_customer_table: function () {
		var rows = this.data.by_customer_group || [];
		if (rows.length === 0) {
			$("#sd-tbl-customer-body").html(
				'<tr><td colspan="6" class="sd-empty">No data</td></tr>'
			);
			return;
		}

		var totalAmount = 0;
		rows.forEach(r => totalAmount += r.amount || 0);

		var html = rows
			.map((row, i) => {
				var pct = totalAmount ? Math.round(((row.amount || 0) / totalAmount) * 100) : 0;
				return `<tr>
					<td>${i + 1}</td>
					<td>${row.group || "Other"}</td>
					<td>${this.fmt_currency(row.amount || 0)}</td>
					<td>${pct}%</td>
					<td>—</td>
					<td>—</td>
				</tr>`;
			})
			.join("");

		html += `<tr class="sd-tbl-total">
			<td></td><td>Total</td>
			<td>${this.fmt_currency(totalAmount)}</td>
			<td>100%</td>
			<td></td><td></td>
		</tr>`;

		$("#sd-tbl-customer-body").html(html);
	},

	// ── Item Group Table ───────────────────────────────────────────────────────
	render_item_group_table: function () {
		var rows = this.data.by_item_group || [];
		if (rows.length === 0) {
			$("#sd-tbl-itemgroup-body").html(
				'<tr><td colspan="6" class="sd-empty">No data</td></tr>'
			);
			return;
		}

		var grandTotal = 0,
			grandQty = 0;
		rows.forEach((r) => {
			grandTotal += r.amount || 0;
			grandQty += r.qty || 0;
		});

		var html = rows
			.map((r, i) => {
				var pct = grandTotal ? Math.round(((r.amount || 0) / grandTotal) * 100) : 0;
				var avg = r.qty ? this.fmt_currency((r.amount || 0) / r.qty) : "—";
				return `<tr>
					<td>${i + 1}</td>
					<td>${r.group || ""}</td>
					<td>${this.fmt_currency(r.amount)}</td>
					<td>${Math.round(r.qty || 0)}</td>
					<td>${avg}</td>
					<td>${pct}%</td>
				</tr>`;
			})
			.join("");

		html += `<tr class="sd-tbl-total">
			<td></td><td>Total</td>
			<td>${this.fmt_currency(grandTotal)}</td>
			<td>${Math.round(grandQty)}</td>
			<td>—</td><td>100%</td>
		</tr>`;

		$("#sd-tbl-itemgroup-body").html(html);
	},

	// ── Donut helper ───────────────────────────────────────────────────────────
	_render_donut: function (canvasId, labels, data, colors) {
		if (this.charts[canvasId]) this.charts[canvasId].destroy();
		this.charts[canvasId] = new Chart(document.getElementById(canvasId), {
			type: "doughnut",
			data: {
				labels: labels,
				datasets: [
					{
						data: data,
						backgroundColor: colors,
						borderWidth: 2,
						borderColor: "#fff",
					},
				],
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				cutout: "65%",
				plugins: { legend: { display: false } },
			},
		});
	},
};
