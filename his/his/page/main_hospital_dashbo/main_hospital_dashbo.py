# Copyright (c) 2026, Al-Ihsan Hospital and contributors
# For license information, please see license.txt

"""Main Hospital Dashboard Desk Page backend.

This module consolidates OPD, Queue Time, IPD, OT, Laboratory, Pharmacy,
Warehouse, Optical, HR/Employee Checkin attendance, Inventory, Sales, and Financial logic
into one management dashboard.  The whitelisted Page API returns
server-rendered HTML plus chart payloads.  The original detailed Script Reports
remain the drill-down destinations.
"""

from __future__ import unicode_literals

import json
import re
import time
from collections import Counter, defaultdict
from datetime import date as date_type, datetime as datetime_type, timedelta
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime


REPORT_NAME = "Main Hospital Dashboard"
DASHBOARD_TITLE = "Main Hospital Dashboard"

OPD_REPORT = "OPD Report"
QUEUE_REPORT = "Queue Time Management"
IPD_REPORT = "IPD Report"
OT_REPORT = "OT Form Report"
LAB_REPORT = "Lab Result Analytics"
HR_REPORT = "Employee List Report"
INVENTORY_REPORT = "Total Stock Summary"
SALES_REPORT = "Sales Dashboard"
FINANCIAL_REPORT = "Financial Dashboard"
ATTENDANCE_REPORT = "Employee Attendance Report"

PAGE_NAME = "main-hospital-dashbo"
ALLOWED_PAGE_ROLES = {
    "System Manager",
    "Healthcare Administrator",
    "Accounts Manager",
}


# Source-level caching keeps the operational queue fresh while avoiding repeated
# GL, inventory, HR and laboratory scans when several managers open the Page.
CACHE_VERSION = "2026-09-16-operations-v5"
SOURCE_CACHE_TTLS = {
    "opd": 45,
    "queue": 10,
    "ipd": 60,
    "ot": 60,
    "lab": 90,
    "stock_operations": 120,
    "optical": 60,
    "staffing": 45,
    "hr": 300,
    "inventory": 180,
    "sales": 120,
    "finance": 120,
}

# Site-specific classification can be adjusted here without changing the SQL.
# Matching is case-insensitive and checks warehouse/item names plus available group/type fields.
PHARMACY_WAREHOUSE_KEYWORDS = (
    "pharmacy",
    "dispensary",
    "drug store",
    "medicine store",
    "medical store",
)
OPTICAL_WAREHOUSE_KEYWORDS = (
    "optical",
    "optometry",
    "eyeglass",
    "eye glass",
)
SUPERMARKET_WAREHOUSE_KEYWORDS = (
    "supermarket",
    "super market",
    "retail store",
)

# The item-group names are exact business rules supplied for this site.
# Warehouse names/types are matched case-insensitively by the keywords above.
WAREHOUSE_ITEM_GROUP_RULES = (
    (PHARMACY_WAREHOUSE_KEYWORDS, ("Drug",)),
    (OPTICAL_WAREHOUSE_KEYWORDS, ("Optical",)),
    (SUPERMARKET_WAREHOUSE_KEYWORDS, ("Supermarket",)),
)
STOCK_EXCEPTION_ITEM_GROUPS = ("Optical", "Drug", "Supermarket")

# The Que value intentionally follows the spelling used in this site's data.
OPTICAL_MEDICAL_DEPARTMENT = "Opthalmology"
OPTICAL_ITEM_KEYWORDS = (
    "eyeglass",
    "eye glass",
    "spectacle",
    "glasses",
    "lens",
)
FRAME_ITEM_KEYWORDS = ("frame", "frames")
DOCTOR_DESIGNATION_KEYWORDS = (
    "doctor",
    "physician",
    "surgeon",
    "Consultant Doctor",
    "medical officer",
    "Specialist"
)
NURSE_DESIGNATION_KEYWORDS = (
    "nurse",
    "nursing",
    "midwife",
    "midwifery",
)
CRITICAL_ITEM_FIELD_CANDIDATES = (
    "is_critical_drug",
    "critical_drug",
    "is_critical_item",
    "critical_item",
    "is_essential_medicine",
    "essential_medicine",
)
ITEM_EXPIRY_FIELD_CANDIDATES = ("expired_date", "expiry_date")
ITEM_REORDER_LEVEL_FIELD_CANDIDATES = (
    "warehouse_reorder_level",
    "reorder_level",
    "minimum_stock",
    "min_qty",
)
PRESCRIPTION_DOCTYPE_CANDIDATES = (
    "Prescription",
    "Patient Prescription",
    "Medication Request",
)
DISPENSED_STATUS_VALUES = (
    "dispensed",
    "completed",
    "delivered",
    "closed",
)

INVALID_DATE_TEXT = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "undefined",
    "not available",
    "not set",
    "yet",
    "-",
    "—",
}

CACHE_FILTER_KEYS = (
    "from_date",
    "to_date",
    "queue_date",
    "company",
    "department",
    "practitioner",
    "hr_department",
    "designation",
    "employment_type",
    "employee_status",
    "warehouse",
    "cost_center",
    "customer",
    "sales_status",
    "finance_book",
)


SOURCE_FILTER_KEYS = {
    "opd": ("from_date", "to_date", "department", "practitioner"),
    "queue": ("queue_date", "department", "practitioner"),
    "ipd": ("from_date", "to_date", "department", "practitioner"),
    "ot": ("from_date", "to_date", "practitioner"),
    "lab": ("from_date", "to_date", "company", "department", "practitioner"),
    "hr": (
        "to_date",
        "company",
        "hr_department",
        "designation",
        "employment_type",
        "employee_status",
    ),
    "stock_operations": ("from_date", "to_date", "queue_date", "company", "warehouse"),
    "optical": ("from_date", "to_date", "company", "department", "practitioner", "warehouse"),
    "staffing": ("queue_date", "company", "hr_department", "designation", "employment_type"),
    "inventory": ("company", "warehouse"),
    "sales": (
        "from_date",
        "to_date",
        "company",
        "cost_center",
        "customer",
        "sales_status",
    ),
    "finance": ("from_date", "to_date", "company", "cost_center", "finance_book"),
}


def _assert_page_access():
    """Protect the aggregate endpoint because its SQL intentionally spans domains."""
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(ALLOWED_PAGE_ROLES):
        frappe.throw(
            _("You are not permitted to open the Main Hospital Dashboard."),
            frappe.PermissionError,
        )


def _safe_getdate(value):
    """Return a date or None without ever raising for N/A-style values."""
    if value is None:
        return None
    if isinstance(value, datetime_type):
        return value.date()
    if isinstance(value, date_type):
        return value

    text_value = str(value).strip()
    if text_value.lower() in INVALID_DATE_TEXT:
        return None

    try:
        return getdate(text_value)
    except Exception:
        return None


def _safe_date_label(value, fallback="—"):
    parsed = _safe_getdate(value)
    return parsed.isoformat() if parsed else fallback


def _normalise_date_filter(parsed, fieldname, default_date):
    value = _safe_getdate(parsed.get(fieldname)) or default_date
    parsed[fieldname] = value.isoformat()
    return value


def _parse_page_filters(filters=None):
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    parsed = frappe._dict(filters or {})
    current_date = now_datetime().date()
    month_start = current_date.replace(day=1)

    _normalise_date_filter(parsed, "from_date", month_start)
    _normalise_date_filter(parsed, "to_date", current_date)
    _normalise_date_filter(parsed, "queue_date", current_date)
    return parsed


@frappe.whitelist()
def get_dashboard_data(filters=None, force_refresh=0):
    """Return the complete HTML dashboard for the custom Desk Page."""
    _assert_page_access()
    started = time.perf_counter()
    parsed_filters = _parse_page_filters(filters)

    _columns, export_rows, html, _chart, _summary, _skip_total_rows = execute(
        parsed_filters,
        force_refresh=bool(cint(force_refresh)),
    )

    cache_hits = list(
        getattr(frappe.local, "main_hospital_dashboard_cache_hits", []) or []
    )

    return {
        "html": html,
        "generated_at": now_datetime().isoformat(),
        "export_row_count": len(export_rows or []),
        "page_name": PAGE_NAME,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "cache_hits": cache_hits,
    }

LAB_PARENT_DOCTYPE = "Lab Result"
LAB_CHILD_DOCTYPE = "Normal Test Result"
LAB_RESULT_TYPE = "Blood"
LAB_DOCSTATUS = 1
LAB_COMPLETED_STATUSES = {"Completed", "Approved", "Verified", "Released", "Final"}

STANDARD_FIELDS = {
    "name",
    "creation",
    "modified",
    "owner",
    "modified_by",
    "docstatus",
    "idx",
    "parent",
    "parenttype",
    "parentfield",
}

LAB_PARENT_FIELD_MAP = {
    "type": ["type", "lab_result_type", "result_type", "sample_type", "category"],
    "patient": ["patient", "patient_id"],
    "practitioner": [
        "practitioner",
        "ordering_practitioner",
        "referring_practitioner",
        "physician",
    ],
    "department": ["department", "medical_department"],
    "status": ["status", "workflow_state", "result_status"],
    "company": ["company"],
    "ordered_at": ["order_date", "requested_date", "sample_collection_time", "creation"],
}

LAB_CHILD_FIELD_MAP = {
    "test_name": ["lab_test_name", "test_name", "parameter", "analyte"],
    "result_value": ["result_value", "result", "value"],
    "normal_range": ["normal_range", "reference_range", "range"],
    "min_value": ["min_normal_value", "min_value", "normal_min", "lower_limit"],
    "max_value": ["max_normal_value", "max_value", "normal_max", "upper_limit"],
}

RANGE_BETWEEN = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
RANGE_UPPER = re.compile(r"^\s*[<≤]\s*=?\s*(-?\d+(?:\.\d+)?)")
RANGE_LOWER = re.compile(r"^\s*[>≥]\s*=?\s*(-?\d+(?:\.\d+)?)")
VALUE_SPAN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
VALUE_LEADING = re.compile(r"^\s*[<>≤≥]?\s*=?\s*(-?\d+(?:\.\d+)?)")

NEGATIVE_TERMS = {
    "nil",
    "nill",
    "none",
    "negative",
    "neg",
    "absent",
    "not detected",
    "nd",
    "normal",
    "no growth",
}


# ---------------------------------------------------------------------------
# Script Report entry point
# ---------------------------------------------------------------------------


def execute(filters=None, force_refresh=False):
    filters = frappe._dict(filters or {})
    _validate_filters(filters)
    frappe.local.main_hospital_dashboard_cache_hits = []

    opd = _cached_source("opd", "OPD", _get_opd, filters, force_refresh)
    queue = _cached_source("queue", "Queue Time", _get_queue_time, filters, force_refresh)
    ipd = _cached_source("ipd", "IPD", _get_ipd, filters, force_refresh)
    ot = _cached_source("ot", "OT", _get_ot, filters, force_refresh)
    lab = _cached_source("lab", "Laboratory", _get_lab, filters, force_refresh)
    stock_operations = _cached_source(
        "stock_operations",
        "Pharmacy and Warehouse",
        _get_stock_operations,
        filters,
        force_refresh,
    )
    pharmacy = stock_operations.get("pharmacy") or _unavailable(
        "Pharmacy", _("Pharmacy stock operations could not be loaded.")
    )
    warehouse = stock_operations.get("warehouse") or _unavailable(
        "Warehouse", _("Warehouse stock operations could not be loaded.")
    )
    optical = _cached_source("optical", "Optical", _get_optical, filters, force_refresh)
    staffing = _cached_source(
        "staffing", "HR Attendance", _get_staffing, filters, force_refresh
    )
    hr = _cached_source("hr", "HR", _get_hr, filters, force_refresh)
    inventory = _cached_source(
        "inventory", "Inventory", _get_inventory, filters, force_refresh
    )
    sales = _cached_source("sales", "Sales", _get_sales, filters, force_refresh)
    finance = _cached_source(
        "finance",
        "Finance",
        _get_finance,
        filters,
        force_refresh,
        sales.get("currency"),
    )

    sources = {
        "opd": opd,
        "queue": queue,
        "ipd": ipd,
        "ot": ot,
        "lab": lab,
        "pharmacy": pharmacy,
        "warehouse": warehouse,
        "optical": optical,
        "staffing": staffing,
        "hr": hr,
        "inventory": inventory,
        "sales": sales,
        "finance": finance,
    }

    alerts = _build_alerts(sources)
    notices = _build_notices(sources)
    overall = _overall_status(alerts, sources)
    cards = _build_kpi_cards(sources, alerts)
    charts = _build_charts(sources)

    payload = {
        "render_id": now_datetime().isoformat(),
        "charts": charts,
        "overall": overall,
        "filters": {
            key: filters.get(key)
            for key in CACHE_FILTER_KEYS
            if filters.get(key)
        },
    }

    message = _render_dashboard(
        filters=filters,
        sources=sources,
        alerts=alerts,
        notices=notices,
        overall=overall,
        cards=cards,
        payload=payload,
    )

    columns = _get_export_columns()
    data = _build_export_rows(sources, alerts, overall)

    # The custom HTML dashboard is in message; no duplicate built-in chart or summary.
    return columns, data, message, None, [], 1


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _validate_filters(filters):
    current_date = now_datetime().date()
    from_date = _safe_getdate(filters.get("from_date"))
    to_date = _safe_getdate(filters.get("to_date"))
    queue_date = _safe_getdate(filters.get("queue_date")) or to_date or current_date

    if not from_date:
        frappe.throw(_("Please set a valid From Date."))
    if not to_date:
        frappe.throw(_("Please set a valid To Date."))
    if from_date > to_date:
        frappe.throw(_("From Date cannot be after To Date."))

    # Keep every downstream query on a canonical YYYY-MM-DD value. This also
    # prevents values such as N/A, yet or an em dash reaching getdate().
    filters.from_date = from_date.isoformat()
    filters.to_date = to_date.isoformat()
    filters.queue_date = queue_date.isoformat()


def _datetime_bounds(filters, prefix):
    from_date = _safe_getdate(filters.get("from_date"))
    to_date = _safe_getdate(filters.get("to_date"))
    if not from_date or not to_date:
        return {}
    return {
        "{0}_from_datetime".format(prefix): "{0} 00:00:00".format(from_date.isoformat()),
        "{0}_to_datetime".format(prefix): "{0} 00:00:00".format(
            (to_date + timedelta(days=1)).isoformat()
        ),
    }


def _dashboard_cache_key(cache_name, filters, extra_args=()):
    payload = {
        key: str(filters.get(key) or "")
        for key in SOURCE_FILTER_KEYS.get(cache_name, CACHE_FILTER_KEYS)
    }
    payload["extra"] = [str(value or "") for value in extra_args]
    digest = sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    site = getattr(frappe.local, "site", "default") or "default"
    return "{0}:main-hospital-dashboard:{1}:{2}:{3}".format(
        site,
        CACHE_VERSION,
        cache_name,
        digest,
    )


def _cached_source(cache_name, label, method, filters, force_refresh=False, *extra_args):
    ttl = cint(SOURCE_CACHE_TTLS.get(cache_name) or 0)
    cache_key = _dashboard_cache_key(cache_name, filters, extra_args)

    if ttl and not force_refresh:
        try:
            cached = frappe.cache().get_value(cache_key)
            if cached is not None:
                hits = getattr(
                    frappe.local, "main_hospital_dashboard_cache_hits", []
                )
                hits.append(cache_name)
                frappe.local.main_hospital_dashboard_cache_hits = hits
                return cached
        except Exception:
            # Cache failure must never make the dashboard unavailable.
            pass

    result = _safe_source(label, method, filters, *extra_args)

    if ttl:
        try:
            frappe.cache().set_value(
                cache_key,
                result,
                expires_in_sec=ttl,
            )
        except Exception:
            pass

    return result


def _safe_source(label, method, *args):
    try:
        return method(*args)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "{0}: {1}".format(REPORT_NAME, label))
        return _unavailable(
            label,
            _("An unexpected error occurred. The error was logged; the other dashboard sections remain available."),
        )


def _unavailable(label, reason, warnings=None):
    return {
        "available": False,
        "label": label,
        "reason": reason,
        "warnings": warnings or [],
        "status": "GREY",
    }


def _current_site():
    return getattr(frappe.local, "site", "default") or "default"


@lru_cache(maxsize=512)
def _doctype_fields_for_site(site, doctype):
    # `site` is part of the cache key because one bench can serve schemas that
    # differ across sites. The current request is already connected to `site`.
    if not frappe.db.exists("DocType", doctype):
        return set()
    meta = frappe.get_meta(doctype)
    return STANDARD_FIELDS.union({df.fieldname for df in meta.fields if df.fieldname})


def _doctype_fields(doctype):
    return _doctype_fields_for_site(_current_site(), doctype)


@lru_cache(maxsize=256)
def _report_exists_for_site(site, report_name):
    return bool(frappe.db.exists("Report", report_name))


def _report_exists(report_name):
    return _report_exists_for_site(_current_site(), report_name)


def _resolve_fields(doctype, field_map):
    fields = _doctype_fields(doctype)
    if not fields:
        return {}

    resolved = {}
    for role, candidates in field_map.items():
        for candidate in candidates:
            if candidate in fields:
                resolved[role] = candidate
                break
    return resolved



@lru_cache(maxsize=512)
def _doctype_fieldtype_for_site(site, doctype, fieldname):
    if fieldname in STANDARD_FIELDS:
        return "Datetime" if fieldname in {"creation", "modified"} else "Data"
    if not frappe.db.exists("DocType", doctype):
        return None
    field = frappe.get_meta(doctype).get_field(fieldname)
    return field.fieldtype if field else None


def _doctype_fieldtype(doctype, fieldname):
    return _doctype_fieldtype_for_site(_current_site(), doctype, fieldname)


def _first_existing_field(doctype, candidates):
    """Return the first candidate field that exists on a DocType."""
    fields = _doctype_fields(doctype)
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None


def _keyword_condition(expressions, keywords, prefix, values):
    """Build a parameterized case-insensitive keyword condition."""
    expressions = [expression for expression in expressions if expression]
    keywords = [str(keyword).strip().lower() for keyword in keywords if str(keyword).strip()]
    if not expressions or not keywords:
        return "0=1"

    clauses = []
    for index, keyword in enumerate(keywords):
        parameter = "{0}_{1}".format(prefix, index)
        values[parameter] = "%{0}%".format(keyword)
        for expression in expressions:
            clauses.append(
                "LOWER(COALESCE(CAST({0} AS CHAR), '')) LIKE %({1})s".format(
                    expression, parameter
                )
            )
    return "({0})".format(" OR ".join(clauses))


def _truthy_sql(expression):
    return "LOWER(COALESCE(CAST({0} AS CHAR), '')) IN ('1', 'yes', 'y', 'true', 'checked')".format(
        expression
    )


def _append_resolved_date_conditions(
    conditions,
    values,
    sql_expression,
    filters,
    prefix,
    datetime_field=False,
):
    """Append index-friendly date conditions for a resolved field."""
    from_date = _safe_getdate(filters.get("from_date"))
    to_date = _safe_getdate(filters.get("to_date"))
    if not from_date and not to_date:
        return

    if datetime_field:
        if from_date:
            values["{0}_from_datetime".format(prefix)] = "{0} 00:00:00".format(
                from_date.isoformat()
            )
            conditions.append(
                "{0} >= %({1}_from_datetime)s".format(sql_expression, prefix)
            )
        if to_date:
            values["{0}_to_datetime".format(prefix)] = "{0} 00:00:00".format(
                (to_date + timedelta(days=1)).isoformat()
            )
            conditions.append(
                "{0} < %({1}_to_datetime)s".format(sql_expression, prefix)
            )
        return

    if from_date:
        values["{0}_from_date".format(prefix)] = from_date.isoformat()
        conditions.append("{0} >= %({1}_from_date)s".format(sql_expression, prefix))
    if to_date:
        values["{0}_to_date".format(prefix)] = to_date.isoformat()
        conditions.append("{0} <= %({1}_to_date)s".format(sql_expression, prefix))


def _source_metric(value, fallback=None):
    """Preserve None for metrics whose source field could not be resolved."""
    return fallback if value is None else value


def _normalise_sql_rows(rows, integer_fields=(), float_fields=()):
    normalized = []
    for raw in rows or []:
        row = frappe._dict(raw)
        for fieldname in integer_fields:
            row[fieldname] = cint(row.get(fieldname))
        for fieldname in float_fields:
            row[fieldname] = flt(row.get(fieldname))
        normalized.append(row)
    return normalized


def _json_for_html(value):
    # Prevent a value containing </script> from terminating the JSON data node.
    return json.dumps(value, ensure_ascii=False, default=str).replace("</", "<\\/")


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _number(value, decimals=0):
    value = flt(value)
    if decimals:
        return "{:,.{places}f}".format(value, places=decimals)
    return "{:,.0f}".format(value)


def _optional_number(value, decimals=0):
    return "—" if value is None else _number(value, decimals)


def _optional_money(value, currency=None):
    return "—" if value is None else _money(value, currency)


def _percent(value, decimals=1):
    if value is None:
        return "—"
    return "{:,.{places}f}%".format(flt(value), places=decimals)


def _money(value, currency=None):
    currency = currency or ""
    amount = "{:,.2f}".format(flt(value))
    return "{0} {1}".format(currency, amount).strip()


def _minutes_display(value):
    if value is None:
        return "—"
    total = max(0, int(round(flt(value))))
    hours, minutes = divmod(total, 60)
    if hours:
        return _("{0}h {1}m").format(hours, minutes)
    return _("{0} min").format(minutes)


def _wait_status(value):
    if value is None:
        return "GREY"
    if flt(value) > 30:
        return "RED"
    if flt(value) > 20:
        return "AMBER"
    return "GREEN"


def _status_rank(status):
    return {"RED": 4, "AMBER": 3, "GREEN": 2, "BLUE": 1, "GREY": 0}.get(status, 0)


def _source_status(source):
    return source.get("status") or ("GREEN" if source.get("available") else "GREY")


# ---------------------------------------------------------------------------
# OPD — preserves the uploaded OPD report's grain and status categories
# ---------------------------------------------------------------------------


def _get_opd(filters):
    doctype = "Que"
    fields = _doctype_fields(doctype)
    if not fields:
        return _unavailable("OPD", _("DocType Que was not found."))

    required = {"date", "practitioner", "department", "que_type", "status"}
    missing = sorted(required - fields)
    if missing:
        return _unavailable(
            "OPD",
            _("Que is missing required fields: {0}").format(", ".join(missing)),
        )

    conditions = ["1=1"]
    values = {}

    if filters.get("from_date"):
        conditions.append("q.`date` >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("q.`date` <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("practitioner"):
        conditions.append("q.`practitioner` = %(practitioner)s")
        values["practitioner"] = filters.practitioner
    if filters.get("department"):
        conditions.append("q.`department` = %(department)s")
        values["department"] = filters.department

    where_clause = " AND ".join(conditions)

    rows = frappe.db.sql(
        """
        SELECT
            COALESCE(NULLIF(TRIM(q.`practitioner`), ''), 'Unassigned') AS practitioner,
            COALESCE(NULLIF(TRIM(q.`department`), ''), 'No Department') AS department,
            SUM(CASE WHEN q.`que_type` = 'New Patient' THEN 1 ELSE 0 END) AS new_patient,
            SUM(CASE WHEN q.`que_type` = 'Follow Up' THEN 1 ELSE 0 END) AS follow_up,
            SUM(CASE WHEN q.`que_type` = 'Refer' THEN 1 ELSE 0 END) AS refer,
            SUM(CASE WHEN q.`que_type` = 'Revisit' THEN 1 ELSE 0 END) AS revisit,
            COUNT(*) AS total_cases,
            SUM(CASE WHEN q.`status` = 'Open' THEN 1 ELSE 0 END) AS open_cases,
            SUM(CASE WHEN q.`status` = 'Closed' THEN 1 ELSE 0 END) AS closed_cases
        FROM `tabQue` q
        WHERE {where_clause}
        GROUP BY q.`practitioner`, q.`department`
        ORDER BY total_cases DESC, practitioner ASC
        """.format(where_clause=where_clause),
        values,
        as_dict=True,
    )

    rows = _normalise_sql_rows(
        rows,
        integer_fields=(
            "new_patient",
            "follow_up",
            "refer",
            "revisit",
            "total_cases",
            "open_cases",
            "closed_cases",
        ),
    )

    trend = frappe.db.sql(
        """
        SELECT
            DATE(q.`date`) AS period,
            COUNT(*) AS total_cases,
            SUM(CASE WHEN q.`status` = 'Open' THEN 1 ELSE 0 END) AS open_cases,
            SUM(CASE WHEN q.`status` = 'Closed' THEN 1 ELSE 0 END) AS closed_cases
        FROM `tabQue` q
        WHERE {where_clause}
        GROUP BY DATE(q.`date`)
        ORDER BY period ASC
        """.format(where_clause=where_clause),
        values,
        as_dict=True,
    )
    trend = _normalise_sql_rows(
        trend,
        integer_fields=("total_cases", "open_cases", "closed_cases"),
    )

    gender = {"male": 0, "female": 0, "other": 0}
    if "gender" in fields:
        gender_row = frappe.db.sql(
            """
            SELECT
                SUM(CASE WHEN q.`gender` = 'Male' THEN 1 ELSE 0 END) AS male,
                SUM(CASE WHEN q.`gender` = 'Female' THEN 1 ELSE 0 END) AS female,
                SUM(CASE WHEN COALESCE(q.`gender`, '') NOT IN ('Male', 'Female') THEN 1 ELSE 0 END) AS other_count
            FROM `tabQue` q
            WHERE {where_clause}
            """.format(where_clause=where_clause),
            values,
            as_dict=True,
        )
        if gender_row:
            gender = {
                "male": cint(gender_row[0].get("male")),
                "female": cint(gender_row[0].get("female")),
                "other": cint(gender_row[0].get("other_count")),
            }

    totals = {
        key: sum(cint(row.get(key)) for row in rows)
        for key in (
            "new_patient",
            "follow_up",
            "refer",
            "revisit",
            "total_cases",
            "open_cases",
            "closed_cases",
        )
    }

    return {
        "available": True,
        "label": "OPD",
        "status": "RED" if totals["open_cases"] else "GREEN",
        "totals": totals,
        "by_doctor": rows[:12],
        "trend": trend,
        "gender": gender,
        "report": OPD_REPORT,
        "report_exists": _report_exists(OPD_REPORT),
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Queue Time Management — chronological patient journey for one selected date
# ---------------------------------------------------------------------------


def _get_queue_time(filters):
    try:
        from ...report.queue_time_managment.queue_time_managment import (
            get_dashboard_source,
        )
    except ImportError:
        return _unavailable(
            "Queue Time",
            _("Queue Time Management report files were not found. Install the queue_time_management report folder."),
        )

    queue_filters = frappe._dict(dict(filters or {}))
    queue_filters.setdefault(
        "queue_date",
        queue_filters.get("to_date") or now_datetime().date().isoformat(),
    )
    return get_dashboard_source(queue_filters)


# ---------------------------------------------------------------------------
# IPD — preserves status counts grouped by admission practitioner/department
# ---------------------------------------------------------------------------


def _get_ipd(filters):
    doctype = "Inpatient Record"
    fields = _doctype_fields(doctype)
    if not fields:
        return _unavailable("IPD", _("DocType Inpatient Record was not found."))

    required = {"status", "admission_practitioner", "creation"}
    missing = sorted(required - fields)
    if missing:
        return _unavailable(
            "IPD",
            _("Inpatient Record is missing required fields: {0}").format(", ".join(missing)),
        )

    practitioner_fields = _doctype_fields("Healthcare Practitioner")
    can_join_department = bool(practitioner_fields and "department" in practitioner_fields)
    warnings = []

    join_sql = ""
    department_expr = "'No Department'"
    if can_join_department:
        join_sql = (
            "LEFT JOIN `tabHealthcare Practitioner` hp "
            "ON hp.name = ipr.`admission_practitioner`"
        )
        department_expr = "COALESCE(NULLIF(TRIM(hp.`department`), ''), 'No Department')"
    elif filters.get("department"):
        warnings.append(
            _("The Department filter could not be applied to IPD because Healthcare Practitioner.department was not found.")
        )

    conditions = ["1=1"]
    values = {}

    ipd_bounds = _datetime_bounds(filters, "ipd")
    if ipd_bounds:
        conditions.append("ipr.`creation` >= %(ipd_from_datetime)s")
        conditions.append("ipr.`creation` < %(ipd_to_datetime)s")
        values.update(ipd_bounds)
    if filters.get("practitioner"):
        conditions.append("ipr.`admission_practitioner` = %(practitioner)s")
        values["practitioner"] = filters.practitioner
    if filters.get("department") and can_join_department:
        conditions.append("hp.`department` = %(department)s")
        values["department"] = filters.department

    where_clause = " AND ".join(conditions)
    practitioner_expr = (
        "COALESCE(NULLIF(TRIM(ipr.`admission_practitioner`), ''), 'No Doctor Assigned')"
    )

    rows = frappe.db.sql(
        """
        SELECT
            {practitioner_expr} AS practitioner,
            {department_expr} AS department,
            SUM(CASE WHEN ipr.`status` = 'Admission Scheduled' THEN 1 ELSE 0 END) AS admission_scheduled,
            SUM(CASE WHEN ipr.`status` = 'Admitted' THEN 1 ELSE 0 END) AS admitted,
            SUM(CASE WHEN ipr.`status` = 'Discharge Scheduled' THEN 1 ELSE 0 END) AS discharge_scheduled,
            SUM(CASE WHEN ipr.`status` = 'Discharged' THEN 1 ELSE 0 END) AS discharged,
            SUM(CASE WHEN ipr.`status` = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled,
            COUNT(*) AS total_patients
        FROM `tabInpatient Record` ipr
        {join_sql}
        WHERE {where_clause}
        GROUP BY {practitioner_expr}, {department_expr}
        ORDER BY total_patients DESC, practitioner ASC
        """.format(
            practitioner_expr=practitioner_expr,
            department_expr=department_expr,
            join_sql=join_sql,
            where_clause=where_clause,
        ),
        values,
        as_dict=True,
    )

    rows = _normalise_sql_rows(
        rows,
        integer_fields=(
            "admission_scheduled",
            "admitted",
            "discharge_scheduled",
            "discharged",
            "cancelled",
            "total_patients",
        ),
    )

    trend = frappe.db.sql(
        """
        SELECT
            DATE(ipr.`creation`) AS period,
            COUNT(*) AS total_patients,
            SUM(CASE WHEN ipr.`status` = 'Admitted' THEN 1 ELSE 0 END) AS admitted,
            SUM(CASE WHEN ipr.`status` = 'Discharged' THEN 1 ELSE 0 END) AS discharged,
            SUM(CASE WHEN ipr.`status` = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled
        FROM `tabInpatient Record` ipr
        {join_sql}
        WHERE {where_clause}
        GROUP BY DATE(ipr.`creation`)
        ORDER BY period ASC
        """.format(join_sql=join_sql, where_clause=where_clause),
        values,
        as_dict=True,
    )
    trend = _normalise_sql_rows(
        trend,
        integer_fields=("total_patients", "admitted", "discharged", "cancelled"),
    )

    totals = {
        key: sum(cint(row.get(key)) for row in rows)
        for key in (
            "admission_scheduled",
            "admitted",
            "discharge_scheduled",
            "discharged",
            "cancelled",
            "total_patients",
        )
    }
    totals["total_doctors"] = len(rows)

    return {
        "available": True,
        "label": "IPD",
        "status": "RED" if totals["cancelled"] else "GREEN",
        "totals": totals,
        "by_doctor": rows[:12],
        "trend": trend,
        "report": IPD_REPORT,
        "report_exists": _report_exists(IPD_REPORT),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Laboratory — keeps analyte and parent-document denominators separate
# ---------------------------------------------------------------------------


def _lab_schema():
    return {
        "parent": _resolve_fields(LAB_PARENT_DOCTYPE, LAB_PARENT_FIELD_MAP),
        "child": _resolve_fields(LAB_CHILD_DOCTYPE, LAB_CHILD_FIELD_MAP),
    }


def _get_lab(filters):
    if not frappe.db.exists("DocType", LAB_PARENT_DOCTYPE):
        return _unavailable("Laboratory", _("DocType Lab Result was not found."))
    if not frappe.db.exists("DocType", LAB_CHILD_DOCTYPE):
        return _unavailable("Laboratory", _("DocType Normal Test Result was not found."))

    schema = _lab_schema()
    parent = schema["parent"]
    child = schema["child"]

    if not parent.get("type"):
        return _unavailable(
            "Laboratory",
            _("No Lab Result type field was resolved, so the Blood-only restriction cannot be applied safely."),
        )

    for role in ("test_name", "result_value"):
        if not child.get(role):
            return _unavailable(
                "Laboratory",
                _("Normal Test Result has no resolved field for {0}.").format(role),
            )

    warnings = []
    if filters.get("company") and not parent.get("company"):
        warnings.append(_("The Company filter could not be applied to laboratory data."))
    if filters.get("department") and not parent.get("department"):
        warnings.append(_("The Department filter could not be applied to laboratory data."))
    if filters.get("practitioner") and not parent.get("practitioner"):
        warnings.append(_("The Practitioner filter could not be applied to laboratory data."))

    select_list = ["lr.name"]
    parent_roles = (
        "patient",
        "practitioner",
        "department",
        "status",
        "company",
        "ordered_at",
        "type",
    )
    child_roles = (
        "test_name",
        "result_value",
        "normal_range",
        "min_value",
        "max_value",
    )

    for role in parent_roles:
        if parent.get(role):
            select_list.append("lr.`{0}` AS `{1}`".format(parent[role], role))
        else:
            select_list.append("NULL AS `{0}`".format(role))

    for role in child_roles:
        if child.get(role):
            select_list.append("ntr.`{0}` AS `{1}`".format(child[role], role))
        else:
            select_list.append("NULL AS `{0}`".format(role))

    conditions = ["lr.docstatus = %(docstatus)s", "lr.`{0}` = %(result_type)s".format(parent["type"])]
    values = {
        "docstatus": LAB_DOCSTATUS,
        "result_type": LAB_RESULT_TYPE,
        "parenttype": LAB_PARENT_DOCTYPE,
    }

    ordered_field = parent.get("ordered_at") or "creation"
    lab_bounds = _datetime_bounds(filters, "lab")
    if lab_bounds:
        conditions.append(
            "lr.`{0}` >= %(lab_from_datetime)s".format(ordered_field)
        )
        conditions.append(
            "lr.`{0}` < %(lab_to_datetime)s".format(ordered_field)
        )
        values.update(lab_bounds)
    if filters.get("company") and parent.get("company"):
        conditions.append("lr.`{0}` = %(company)s".format(parent["company"]))
        values["company"] = filters.company
    if filters.get("department") and parent.get("department"):
        conditions.append("lr.`{0}` = %(department)s".format(parent["department"]))
        values["department"] = filters.department
    if filters.get("practitioner") and parent.get("practitioner"):
        conditions.append("lr.`{0}` = %(practitioner)s".format(parent["practitioner"]))
        values["practitioner"] = filters.practitioner

    rows = frappe.db.sql(
        """
        SELECT {select_list}
        FROM `tab{parent_doctype}` lr
        INNER JOIN `tab{child_doctype}` ntr
            ON ntr.parent = lr.name
           AND ntr.parenttype = %(parenttype)s
        WHERE {where_clause}
        ORDER BY lr.`{ordered_field}` ASC, lr.name ASC, ntr.idx ASC
        """.format(
            select_list=", ".join(select_list),
            parent_doctype=LAB_PARENT_DOCTYPE,
            child_doctype=LAB_CHILD_DOCTYPE,
            where_clause=" AND ".join(conditions),
            ordered_field=ordered_field,
        ),
        values,
        as_dict=True,
    )

    for row in rows:
        row["result_flag"] = _lab_result_flag(row, child)

    analytes = len(rows)
    document_names = {row.get("name") for row in rows if row.get("name")}
    patients = {row.get("patient") for row in rows if row.get("patient")}
    completed_documents = {
        row.get("name")
        for row in rows
        if row.get("name") and row.get("status") in LAB_COMPLETED_STATUSES
    }

    normal = sum(1 for row in rows if row.get("result_flag") == "Normal")
    abnormal = sum(1 for row in rows if row.get("result_flag") in {"High", "Low", "Abnormal"})
    not_evaluated = analytes - normal - abnormal
    evaluated = normal + abnormal
    abnormal_rate = (abnormal / evaluated * 100) if evaluated else None
    evaluation_coverage = (evaluated / analytes * 100) if analytes else None

    test_counter = Counter((row.get("test_name") or _("Not Set")) for row in rows)
    top_tests = [
        {"label": label, "value": count}
        for label, count in test_counter.most_common(10)
    ]

    daily = defaultdict(lambda: {"normal": 0, "abnormal": 0, "not_evaluated": 0})
    by_department = defaultdict(lambda: {"normal": 0, "abnormal": 0})

    for row in rows:
        ordered_at = _safe_getdate(row.get("ordered_at"))
        if ordered_at:
            period = ordered_at.isoformat()
            if row.get("result_flag") == "Normal":
                daily[period]["normal"] += 1
            elif row.get("result_flag") in {"High", "Low", "Abnormal"}:
                daily[period]["abnormal"] += 1
            else:
                daily[period]["not_evaluated"] += 1

        department = row.get("department") or _("Not Set")
        if row.get("result_flag") == "Normal":
            by_department[department]["normal"] += 1
        elif row.get("result_flag") in {"High", "Low", "Abnormal"}:
            by_department[department]["abnormal"] += 1

    trend = [
        {
            "period": period,
            "normal": values_["normal"],
            "abnormal": values_["abnormal"],
            "not_evaluated": values_["not_evaluated"],
        }
        for period, values_ in sorted(daily.items())
    ]

    department_rows = [
        {
            "department": department,
            "normal": values_["normal"],
            "abnormal": values_["abnormal"],
            "total": values_["normal"] + values_["abnormal"],
        }
        for department, values_ in by_department.items()
    ]
    department_rows.sort(key=lambda row: row["total"], reverse=True)

    if abnormal_rate is None:
        status = "GREY"
    elif abnormal_rate > 40:
        status = "RED"
    elif abnormal_rate > 0:
        status = "AMBER"
    else:
        status = "GREEN"

    return {
        "available": True,
        "label": "Laboratory",
        "status": status,
        "result_type": LAB_RESULT_TYPE,
        "totals": {
            "analytes": analytes,
            "documents": len(document_names),
            "patients": len(patients),
            "completed_documents": len(completed_documents),
            "normal": normal,
            "abnormal": abnormal,
            "not_evaluated": not_evaluated,
            "abnormal_rate": abnormal_rate,
            "evaluation_coverage": evaluation_coverage,
        },
        "top_tests": top_tests,
        "by_department": department_rows[:10],
        "trend": trend,
        "report": LAB_REPORT,
        "report_exists": _report_exists(LAB_REPORT),
        "warnings": warnings,
    }


def _normalise_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _to_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        pass

    match = VALUE_SPAN.match(text)
    if match:
        return float(match.group(2))
    match = VALUE_LEADING.match(text)
    if match:
        return float(match.group(1))
    return None


def _classify_by_bounds(value, low, high):
    if value is None:
        return None
    if low is not None and value < low:
        return "Low"
    if high is not None and value > high:
        return "High"
    if low is None and high is None:
        return None
    return "Normal"


def _classify_by_text(value, normal_range):
    if value is None:
        return None
    text = str(normal_range or "").strip()
    if not text:
        return None

    match = RANGE_BETWEEN.match(text)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if value < low:
            return "Low"
        return "Normal" if value <= high else "High"

    match = RANGE_UPPER.match(text)
    if match:
        return "Normal" if value <= float(match.group(1)) else "High"

    match = RANGE_LOWER.match(text)
    if match:
        return "Normal" if value >= float(match.group(1)) else "Low"

    return None


def _classify_qualitative(result_value, normal_range):
    result = _normalise_text(result_value)
    reference = _normalise_text(normal_range)
    if not result or not reference:
        return None
    if result == reference:
        return "Normal"

    result_negative = result in NEGATIVE_TERMS
    reference_negative = reference in NEGATIVE_TERMS
    if reference_negative and result_negative:
        return "Normal"
    if reference_negative and not result_negative:
        return "Abnormal"
    return None


def _lab_result_flag(row, child):
    value = _to_float(row.get("result_value"))
    has_bounds = bool(child.get("min_value") or child.get("max_value"))

    verdict = None
    if has_bounds:
        verdict = _classify_by_bounds(
            value,
            _to_float(row.get("min_value")),
            _to_float(row.get("max_value")),
        )
    if verdict is None and child.get("normal_range"):
        verdict = _classify_by_text(value, row.get("normal_range"))
    if verdict is None and child.get("normal_range"):
        verdict = _classify_qualitative(row.get("result_value"), row.get("normal_range"))

    return verdict or "Not Evaluated"



# ---------------------------------------------------------------------------
# OT Form — preserves the supplied OT Form query and adds dashboard summaries
# ---------------------------------------------------------------------------


OT_DONE_VALUES = {
    "1",
    "yes",
    "y",
    "true",
    "done",
    "completed",
    "complete",
    "signed",
    "admitted",
    "ready",
}


def _ot_is_done(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return flt(value) > 0
    return str(value).strip().lower() in OT_DONE_VALUES


def _get_ot(filters):
    doctype = "OT Form"
    fields = _doctype_fields(doctype)
    if not fields:
        return _unavailable("OT", _("DocType OT Form was not found."))

    required = {
        "date",
        "patient",
        "patient_name",
        "doctor",
        "procedure_name",
        "eye_side",
        "amount",
        "laboratory_tests_done",
        "iol_calculation_done",
        "consent_form_signed",
        "admission_done",
        "call_status",
        "remarks",
    }
    missing = sorted(required - fields)
    if missing:
        return _unavailable(
            "OT",
            _("OT Form is missing required fields: {0}").format(", ".join(missing)),
        )

    patient_fields = _doctype_fields("Patient")
    can_join_patient = bool(patient_fields)
    warnings = []

    patient_join = ""
    if can_join_patient:
        patient_join = "LEFT JOIN `tabPatient` p ON p.name = ot.`patient`"
    else:
        warnings.append(
            _("Patient was not found, so mobile number, age and sex are unavailable in the OT section.")
        )

    def patient_expr(fieldname):
        if can_join_patient and fieldname in patient_fields:
            return "p.`{0}`".format(fieldname)
        return "NULL"

    conditions = ["1=1"]
    values = {}

    if filters.get("from_date"):
        conditions.append("ot.`date` >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("ot.`date` <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("practitioner"):
        conditions.append("ot.`doctor` = %(practitioner)s")
        values["practitioner"] = filters.practitioner

    rows = frappe.db.sql(
        """
        SELECT
            ot.name,
            ot.`date` AS operation_date,
            ot.`patient` AS patient,
            ot.`patient_name` AS patient_name,
            {mobile_expr} AS mobile_no,
            {age_expr} AS patient_age,
            {age_type_expr} AS age_type,
            {sex_expr} AS sex,
            ot.`doctor` AS doctor,
            ot.`procedure_name` AS procedure_name,
            ot.`eye_side` AS eye_side,
            ot.`amount` AS amount,
            ot.`laboratory_tests_done` AS laboratory_tests_done,
            ot.`iol_calculation_done` AS iol_calculation_done,
            ot.`consent_form_signed` AS consent_form_signed,
            ot.`admission_done` AS admission_done,
            ot.`call_status` AS call_status,
            ot.`remarks` AS remarks
        FROM `tabOT Form` ot
        {patient_join}
        WHERE {where_clause}
        ORDER BY ot.`date` DESC, ot.name DESC
        """.format(
            mobile_expr=patient_expr("mobile_no"),
            age_expr=patient_expr("p_age"),
            age_type_expr=patient_expr("age_type"),
            sex_expr=patient_expr("sex"),
            patient_join=patient_join,
            where_clause=" AND ".join(conditions),
        ),
        values,
        as_dict=True,
    )
    rows = _normalise_sql_rows(rows, float_fields=("amount",))

    readiness_fields = (
        ("laboratory_tests_done", _("Laboratory tests")),
        ("iol_calculation_done", _("IOL calculation")),
        ("consent_form_signed", _("Consent form")),
        ("admission_done", _("Admission")),
    )

    procedure_counter = Counter()
    eye_counter = Counter()
    call_counter = Counter()
    daily = defaultdict(lambda: {"cases": 0, "amount": 0.0})

    for row in rows:
        pending = [
            label
            for fieldname, label in readiness_fields
            if not _ot_is_done(row.get(fieldname))
        ]
        row["preop_ready"] = not pending
        row["pending_requirements"] = pending

        procedure_counter[row.get("procedure_name") or _("Not Set")] += 1
        eye_counter[row.get("eye_side") or _("Not Set")] += 1
        call_counter[row.get("call_status") or _("Not Set")] += 1

        period = _safe_date_label(row.get("operation_date"), fallback=_("Not Set"))
        daily[period]["cases"] += 1
        daily[period]["amount"] += flt(row.get("amount"))

    total_cases = len(rows)
    unique_patients = len({row.get("patient") for row in rows if row.get("patient")})
    lab_done = sum(1 for row in rows if _ot_is_done(row.get("laboratory_tests_done")))
    iol_done = sum(1 for row in rows if _ot_is_done(row.get("iol_calculation_done")))
    consent_done = sum(1 for row in rows if _ot_is_done(row.get("consent_form_signed")))
    admission_done = sum(1 for row in rows if _ot_is_done(row.get("admission_done")))
    preop_ready = sum(1 for row in rows if row.get("preop_ready"))
    pending_preop = total_cases - preop_ready

    currency = None
    if filters.get("company"):
        currency = frappe.db.get_value("Company", filters.company, "default_currency")
    currency = currency or frappe.defaults.get_global_default("currency") or ""

    return {
        "available": True,
        "label": "OT",
        "status": "GREY" if not rows else ("AMBER" if pending_preop else "GREEN"),
        "currency": currency,
        "totals": {
            "cases": total_cases,
            "patients": unique_patients,
            "amount": sum(flt(row.get("amount")) for row in rows),
            "lab_done": lab_done,
            "iol_done": iol_done,
            "consent_done": consent_done,
            "admission_done": admission_done,
            "preop_ready": preop_ready,
            "pending_preop": pending_preop,
        },
        "trend": [
            {
                "period": period,
                "cases": values_["cases"],
                "amount": round(values_["amount"], 2),
            }
            for period, values_ in sorted(daily.items())
        ],
        "top_procedures": [
            {"label": label, "value": count}
            for label, count in procedure_counter.most_common(10)
        ],
        "eye_sides": [
            {"label": label, "value": count}
            for label, count in eye_counter.most_common()
        ],
        "call_statuses": [
            {"label": label, "value": count}
            for label, count in call_counter.most_common()
        ],
        "recent": rows[:15],
        "report": OT_REPORT,
        "report_exists": _report_exists(OT_REPORT),
        "warnings": warnings,
    }



# ---------------------------------------------------------------------------
# Pharmacy and Warehouse daily exception reports
# ---------------------------------------------------------------------------


def _warehouse_item_groups(warehouse_row):
    """Resolve the item groups owned by one warehouse from the site mapping."""
    haystack = " ".join(
        str(warehouse_row.get(fieldname) or "")
        for fieldname in ("name", "warehouse_name", "warehouse_type")
    ).lower()

    groups = []
    for warehouse_keywords, item_groups in WAREHOUSE_ITEM_GROUP_RULES:
        if any(keyword.lower() in haystack for keyword in warehouse_keywords):
            groups.extend(item_groups)

    # Preserve rule order while removing duplicates.
    return tuple(dict.fromkeys(groups))


def _enabled_warehouse_rows(filters, required_item_groups=None):
    """Return enabled leaf warehouses that have an explicit item-group mapping."""
    fields = _doctype_fields("Warehouse")
    if not fields:
        return []

    query_fields = ["name"]
    for fieldname in ("warehouse_name", "warehouse_type", "company", "is_group", "disabled"):
        if fieldname in fields:
            query_fields.append(fieldname)

    warehouse_filters = {}
    if "is_group" in fields:
        warehouse_filters["is_group"] = 0
    if "disabled" in fields:
        warehouse_filters["disabled"] = 0
    if filters.get("company") and "company" in fields:
        warehouse_filters["company"] = filters.company

    selected = _get_inventory_warehouse_list(filters)
    if selected:
        warehouse_filters["name"] = ["in", selected]

    rows = frappe.get_all(
        "Warehouse",
        filters=warehouse_filters,
        fields=query_fields,
        order_by="name asc",
        limit_page_length=0,
    )

    required = {str(value).strip().lower() for value in (required_item_groups or ())}
    mapped_rows = []
    for raw in rows:
        row = frappe._dict(raw)
        groups = _warehouse_item_groups(row)
        if required:
            groups = tuple(group for group in groups if group.lower() in required)
        if not groups:
            continue
        row.allowed_item_groups = groups
        mapped_rows.append(row)

    return mapped_rows


def _warehouse_item_group_condition(warehouse_rows, values, prefix):
    """Build exact Warehouse -> Item Group pairs for SQL stock queries."""
    clauses = []
    for index, row in enumerate(warehouse_rows or []):
        warehouse = row.get("name")
        groups = tuple(
            str(group).strip().lower()
            for group in (row.get("allowed_item_groups") or ())
            if str(group).strip()
        )
        if not warehouse or not groups:
            continue

        warehouse_key = "{0}_warehouse_{1}".format(prefix, index)
        group_key = "{0}_groups_{1}".format(prefix, index)
        values[warehouse_key] = warehouse
        values[group_key] = groups
        clauses.append(
            "(b.`warehouse` = %({warehouse_key})s "
            "AND LOWER(TRIM(i.`item_group`)) IN %({group_key})s)".format(
                warehouse_key=warehouse_key,
                group_key=group_key,
            )
        )

    return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"


def _stock_exception_source(filters, warehouse_rows, label):
    bin_fields = _doctype_fields("Bin")
    item_fields = _doctype_fields("Item")
    warehouse_fields = _doctype_fields("Warehouse")

    if not bin_fields or not item_fields or not warehouse_fields:
        return _unavailable(
            label,
            _("Bin, Item, and Warehouse are required for stock exception analysis."),
        )

    required_bin = {"item_code", "warehouse", "actual_qty"}
    required_item = {"item_code", "is_stock_item", "item_group"}
    if not required_bin.issubset(bin_fields):
        return _unavailable(
            label,
            _("Bin is missing required fields: {0}").format(
                ", ".join(sorted(required_bin - bin_fields))
            ),
        )
    if not required_item.issubset(item_fields):
        return _unavailable(
            label,
            _("Item is missing required fields: {0}").format(
                ", ".join(sorted(required_item - item_fields))
            ),
        )

    warehouse_names = [row.get("name") for row in warehouse_rows if row.get("name")]
    if not warehouse_names:
        return _unavailable(
            label,
            _(
                "No enabled leaf warehouse matched the configured Pharmacy→Drug, "
                "Optical→Optical, or Supermarket→Supermarket mapping."
            ),
        )

    warnings = []
    today = now_datetime().date()
    values = {
        "scope_warehouses": tuple(warehouse_names),
        "today": today.isoformat(),
        "near_expiry_date": (today + timedelta(days=90)).isoformat(),
    }
    conditions = [
        "b.`warehouse` IN %(scope_warehouses)s",
        "COALESCE(i.`is_stock_item`, 0) = 1",
        _warehouse_item_group_condition(
            warehouse_rows,
            values,
            re.sub(r"[^a-z0-9]+", "_", label.lower()),
        ),
    ]
    if "disabled" in item_fields:
        conditions.append("COALESCE(i.`disabled`, 0) = 0")
    if "disabled" in warehouse_fields:
        conditions.append("COALESCE(w.`disabled`, 0) = 0")
    if "is_group" in warehouse_fields:
        conditions.append("COALESCE(w.`is_group`, 0) = 0")

    qty_expr = "COALESCE(b.`actual_qty`, 0)"
    if "stock_value" in bin_fields:
        stock_value_expr = "COALESCE(b.`stock_value`, 0)"
    elif "valuation_rate" in bin_fields:
        stock_value_expr = "({0} * COALESCE(b.`valuation_rate`, 0))".format(qty_expr)
        warnings.append(
            _("Bin.stock_value is unavailable; stock value is estimated from quantity × valuation rate.")
        )
    else:
        stock_value_expr = "0"
        warnings.append(_("Stock value fields were not found on Bin."))

    item_name_expr = (
        "COALESCE(NULLIF(TRIM(i.`item_name`), ''), i.`item_code`)"
        if "item_name" in item_fields
        else "i.`item_code`"
    )
    item_group_expr = "COALESCE(NULLIF(TRIM(i.`item_group`), ''), 'Not Set')"

    reorder_join = ""
    reorder_level_expr = None
    reorder_fields = _doctype_fields("Item Reorder")
    reorder_level_field = next(
        (
            candidate
            for candidate in ITEM_REORDER_LEVEL_FIELD_CANDIDATES
            if candidate in reorder_fields
        ),
        None,
    )
    if reorder_fields and {"parent", "warehouse"}.issubset(reorder_fields) and reorder_level_field:
        reorder_join = """
        LEFT JOIN (
            SELECT
                ir.`parent` AS item_code,
                ir.`warehouse` AS warehouse,
                MAX(COALESCE(ir.`{reorder_level_field}`, 0)) AS reorder_level
            FROM `tabItem Reorder` ir
            WHERE ir.`warehouse` IS NOT NULL
              AND ir.`warehouse` != ''
            GROUP BY ir.`parent`, ir.`warehouse`
        ) reorder_rule
            ON reorder_rule.item_code = i.`item_code`
           AND reorder_rule.warehouse = b.`warehouse`
        """.format(reorder_level_field=reorder_level_field)
        reorder_level_expr = "COALESCE(reorder_rule.reorder_level, 0)"
    else:
        item_reorder_field = next(
            (
                candidate
                for candidate in ITEM_REORDER_LEVEL_FIELD_CANDIDATES
                if candidate in item_fields
            ),
            None,
        )
        if item_reorder_field:
            reorder_level_expr = "COALESCE(i.`{0}`, 0)".format(item_reorder_field)
        else:
            warnings.append(
                _("No reorder-level field was resolved; Low Stock is shown as unavailable rather than zero.")
            )

    expiry_field = next(
        (candidate for candidate in ITEM_EXPIRY_FIELD_CANDIDATES if candidate in item_fields),
        None,
    )
    expiry_expr = None
    if expiry_field:
        expiry_expr = """
        CASE
            WHEN CAST(i.`{field}` AS CHAR) REGEXP '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
             AND CAST(i.`{field}` AS CHAR) <> '0000-00-00'
            THEN DATE(i.`{field}`)
            ELSE NULL
        END
        """.format(field=expiry_field).strip()
    else:
        warnings.append(
            _(
                "No Item expiry field was resolved. Stockout falls back to quantity only; "
                "Near Expiry and Expired Stock are unavailable."
            )
        )

    critical_field = next(
        (candidate for candidate in CRITICAL_ITEM_FIELD_CANDIDATES if candidate in item_fields),
        None,
    )
    critical_expr = _truthy_sql("i.`{0}`".format(critical_field)) if critical_field else None
    if not critical_field:
        warnings.append(
            _("No critical-drug flag was resolved on Item; Critical Drugs Unavailable is shown as unavailable.")
        )

    expired_condition = None
    near_condition = None
    if expiry_expr:
        expired_condition = "{qty} > 0 AND {expiry} < %(today)s".format(
            qty=qty_expr,
            expiry=expiry_expr,
        )
        near_condition = (
            "{qty} > 0 AND {expiry} >= %(today)s "
            "AND {expiry} < %(near_expiry_date)s"
        ).format(qty=qty_expr, expiry=expiry_expr)

    stockout_condition = "{0} <= 0".format(qty_expr)
    if expiry_expr:
        stockout_condition = "({qty} <= 0 OR ({expiry} IS NOT NULL AND {expiry} < %(today)s))".format(
            qty=qty_expr,
            expiry=expiry_expr,
        )

    usable_condition = "{0} > 0".format(qty_expr)
    if expiry_expr:
        usable_condition += " AND ({0} IS NULL OR {0} >= %(today)s)".format(expiry_expr)

    low_condition = None
    if reorder_level_expr:
        low_condition = "{usable} AND {level} > 0 AND {qty} <= {level}".format(
            usable=usable_condition,
            qty=qty_expr,
            level=reorder_level_expr,
        )

    critical_condition = None
    if critical_expr:
        critical_condition = (
            "({critical}) AND LOWER(TRIM(i.`item_group`)) = 'drug' "
            "AND ({stockout})"
        ).format(critical=critical_expr, stockout=stockout_condition)

    def count_expr(condition):
        return (
            "COUNT(DISTINCT CASE WHEN {0} THEN i.`item_code` END)".format(condition)
            if condition
            else "NULL"
        )

    low_count_expr = count_expr(low_condition)
    near_count_expr = count_expr(near_condition)
    expired_count_expr = count_expr(expired_condition)
    critical_count_expr = count_expr(critical_condition)
    near_value_expr = (
        "COALESCE(SUM(CASE WHEN {0} THEN {1} ELSE 0 END), 0)".format(
            near_condition, stock_value_expr
        )
        if near_condition
        else "NULL"
    )
    expired_value_expr = (
        "COALESCE(SUM(CASE WHEN {0} THEN {1} ELSE 0 END), 0)".format(
            expired_condition, stock_value_expr
        )
        if expired_condition
        else "NULL"
    )

    from_sql = """
        FROM `tabBin` b
        INNER JOIN `tabItem` i
            ON i.`item_code` = b.`item_code`
        INNER JOIN `tabWarehouse` w
            ON w.name = b.`warehouse`
        {reorder_join}
        WHERE {where_clause}
    """.format(
        reorder_join=reorder_join,
        where_clause=" AND ".join(conditions),
    )

    summary_rows = frappe.db.sql(
        """
        SELECT
            COUNT(DISTINCT i.`item_code`) AS tracked_items,
            COUNT(DISTINCT b.`warehouse`) AS warehouses,
            COUNT(DISTINCT CASE WHEN {stockout} THEN i.`item_code` END) AS stockouts,
            {low_count} AS low_stock,
            {critical_count} AS critical_unavailable,
            {near_count} AS near_expiry,
            {near_value} AS near_expiry_value,
            {expired_count} AS expired_stock,
            {expired_value} AS expired_stock_value,
            COALESCE(SUM({stock_value}), 0) AS total_stock_value
        {from_sql}
        """.format(
            stockout=stockout_condition,
            low_count=low_count_expr,
            critical_count=critical_count_expr,
            near_count=near_count_expr,
            near_value=near_value_expr,
            expired_count=expired_count_expr,
            expired_value=expired_value_expr,
            stock_value=stock_value_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    summary = frappe._dict(summary_rows[0] if summary_rows else {})

    by_warehouse = frappe.db.sql(
        """
        SELECT
            b.`warehouse` AS warehouse,
            GROUP_CONCAT(DISTINCT {item_group} ORDER BY {item_group} SEPARATOR ', ') AS item_groups,
            COUNT(DISTINCT CASE WHEN {stockout} THEN i.`item_code` END) AS stockouts,
            {low_count} AS low_stock,
            {near_count} AS near_expiry,
            {expired_count} AS expired_stock
        {from_sql}
        GROUP BY b.`warehouse`
        ORDER BY stockouts DESC, low_stock DESC, b.`warehouse` ASC
        LIMIT 100
        """.format(
            item_group=item_group_expr,
            stockout=stockout_condition,
            low_count=low_count_expr,
            near_count=near_count_expr,
            expired_count=expired_count_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    normalized_by_warehouse = []
    for raw in by_warehouse:
        row = frappe._dict(raw)
        row.stockouts = cint(row.get("stockouts"))
        row.low_stock = None if not low_condition else cint(row.get("low_stock"))
        row.near_expiry = None if not near_condition else cint(row.get("near_expiry"))
        row.expired_stock = None if not expired_condition else cint(row.get("expired_stock"))
        normalized_by_warehouse.append(row)

    exception_conditions = ["({0})".format(stockout_condition)]
    for condition in (low_condition, near_condition):
        if condition:
            exception_conditions.append("({0})".format(condition))

    reorder_select = reorder_level_expr or "NULL"
    expiry_select = expiry_expr or "NULL"
    critical_select = (
        "CASE WHEN {0} THEN 1 ELSE 0 END".format(critical_expr)
        if critical_expr
        else "NULL"
    )
    detail_rows = frappe.db.sql(
        """
        SELECT
            b.`warehouse` AS warehouse,
            i.`item_code` AS item_code,
            {item_name} AS item_name,
            {item_group} AS item_group,
            {qty} AS actual_qty,
            {reorder_level} AS reorder_level,
            {expiry} AS expiry_date,
            {stock_value} AS stock_value,
            {critical} AS is_critical,
            CASE
                {expired_case}
                WHEN {qty} <= 0 THEN 'Stockout'
                {low_case}
                {near_case}
                ELSE 'Exception'
            END AS exception_type
        {from_sql}
          AND ({exception_where})
        ORDER BY
            CASE
                {expired_order}
                WHEN {qty} <= 0 THEN 2
                {low_order}
                {near_order}
                ELSE 5
            END,
            stock_value DESC,
            i.`item_code` ASC
        LIMIT 80
        """.format(
            item_name=item_name_expr,
            item_group=item_group_expr,
            qty=qty_expr,
            reorder_level=reorder_select,
            expiry=expiry_select,
            stock_value=stock_value_expr,
            critical=critical_select,
            expired_case=(
                "WHEN {0} THEN 'Expired / Stockout'".format(expired_condition)
                if expired_condition
                else ""
            ),
            low_case=(
                "WHEN {0} THEN 'Low Stock'".format(low_condition)
                if low_condition
                else ""
            ),
            near_case=(
                "WHEN {0} THEN 'Near Expiry'".format(near_condition)
                if near_condition
                else ""
            ),
            expired_order=(
                "WHEN {0} THEN 1".format(expired_condition)
                if expired_condition
                else ""
            ),
            low_order=(
                "WHEN {0} THEN 3".format(low_condition)
                if low_condition
                else ""
            ),
            near_order=(
                "WHEN {0} THEN 4".format(near_condition)
                if near_condition
                else ""
            ),
            from_sql=from_sql,
            exception_where=" OR ".join(exception_conditions),
        ),
        values,
        as_dict=True,
    )
    detail_rows = _normalise_sql_rows(
        detail_rows,
        float_fields=("actual_qty", "reorder_level", "stock_value"),
    )

    currency = None
    if filters.get("company"):
        currency = frappe.db.get_value("Company", filters.company, "default_currency")
    currency = currency or frappe.defaults.get_global_default("currency") or ""

    totals = {
        "tracked_items": cint(summary.get("tracked_items")),
        "warehouses": cint(summary.get("warehouses")),
        "stockouts": cint(summary.get("stockouts")),
        "low_stock": None if not low_condition else cint(summary.get("low_stock")),
        "critical_unavailable": (
            None if not critical_condition else cint(summary.get("critical_unavailable"))
        ),
        "near_expiry": None if not near_condition else cint(summary.get("near_expiry")),
        "near_expiry_value": (
            None if not near_condition else round(flt(summary.get("near_expiry_value")), 2)
        ),
        "expired_stock": (
            None if not expired_condition else cint(summary.get("expired_stock"))
        ),
        "expired_stock_value": (
            None if not expired_condition else round(flt(summary.get("expired_stock_value")), 2)
        ),
        "total_stock_value": round(flt(summary.get("total_stock_value")), 2),
    }

    if totals["stockouts"] or (totals["critical_unavailable"] or 0) or (totals["expired_stock"] or 0):
        status = "RED"
    elif (totals["low_stock"] or 0) or (totals["near_expiry"] or 0):
        status = "AMBER"
    else:
        status = "GREEN" if totals["tracked_items"] else "GREY"

    warnings.extend(
        [
            _(
                "Stock exceptions are restricted to mapped Item Groups: "
                "Pharmacy→Drug, Optical→Optical, and Supermarket→Supermarket."
            ),
            _(
                "An item is counted as Stockout when quantity is zero/negative or its resolved Item expiry date is before today."
            ),
            _(
                "Stockouts use existing Bin item/warehouse lines. Items with no Bin history are not inferred as stockouts."
            ),
        ]
    )

    return {
        "available": True,
        "label": label,
        "status": status,
        "currency": currency,
        "warehouse_names": warehouse_names,
        "warehouse_item_groups": [
            {
                "warehouse": row.get("name"),
                "item_groups": list(row.get("allowed_item_groups") or ()),
            }
            for row in warehouse_rows
        ],
        "totals": totals,
        "by_warehouse": normalized_by_warehouse,
        "exceptions": detail_rows,
        "report": INVENTORY_REPORT,
        "report_exists": _report_exists(INVENTORY_REPORT),
        "warnings": warnings,
    }



def _get_pending_purchase_orders(filters, warehouse_names, label):
    po_fields = _doctype_fields("Purchase Order")
    poi_fields = _doctype_fields("Purchase Order Item")
    if not po_fields:
        return None, [], _("Purchase Order was not found; Pending POs are unavailable.")

    conditions = ["po.docstatus = 1"]
    values = {}
    if "status" in po_fields:
        # This intentionally follows the supplied rule exactly.
        conditions.append("COALESCE(po.`status`, '') != 'Completed'")
    else:
        return None, [], _("Purchase Order.status was not found; Pending POs are unavailable.")

    if filters.get("company") and "company" in po_fields:
        conditions.append("po.`company` = %(po_company)s")
        values["po_company"] = filters.company

    warehouse_field = next(
        (candidate for candidate in ("warehouse", "target_warehouse") if candidate in poi_fields),
        None,
    )
    join_sql = ""
    warning = None
    if warehouse_names and warehouse_field and {"parent", "parenttype"}.issubset(poi_fields):
        join_sql = """
        INNER JOIN `tabPurchase Order Item` poi
            ON poi.`parent` = po.name
           AND poi.`parenttype` = 'Purchase Order'
        """
        conditions.append("poi.`{0}` IN %(po_warehouses)s".format(warehouse_field))
        values["po_warehouses"] = tuple(warehouse_names)
    elif warehouse_names:
        warning = _(
            "Purchase Order Item warehouse could not be resolved; {0} Pending POs are company-wide."
        ).format(label)

    supplier_expr = "po.`supplier`" if "supplier" in po_fields else "NULL"
    date_field = next(
        (field for field in ("transaction_date", "schedule_date", "creation") if field in po_fields),
        "creation",
    )
    total_field = next(
        (field for field in ("base_grand_total", "grand_total", "rounded_total") if field in po_fields),
        None,
    )
    total_expr = "COALESCE(po.`{0}`, 0)".format(total_field) if total_field else "0"

    rows = frappe.db.sql(
        """
        SELECT
            po.name,
            MAX({supplier}) AS supplier,
            MAX(po.`{date_field}`) AS transaction_date,
            MAX(po.`status`) AS status,
            MAX({total}) AS total
        FROM `tabPurchase Order` po
        {join_sql}
        WHERE {where_clause}
        GROUP BY po.name
        ORDER BY transaction_date DESC, po.name DESC
        LIMIT 20
        """.format(
            supplier=supplier_expr,
            date_field=date_field,
            total=total_expr,
            join_sql=join_sql,
            where_clause=" AND ".join(conditions),
        ),
        values,
        as_dict=True,
    )
    rows = _normalise_sql_rows(rows, float_fields=("total",))

    count_rows = frappe.db.sql(
        """
        SELECT COUNT(DISTINCT po.name) AS pending_count
        FROM `tabPurchase Order` po
        {join_sql}
        WHERE {where_clause}
        """.format(join_sql=join_sql, where_clause=" AND ".join(conditions)),
        values,
        as_dict=True,
    )
    count = cint(count_rows[0].get("pending_count")) if count_rows else 0
    return count, rows, warning


def _get_dispensed_prescriptions(filters):
    warnings = []
    for doctype in PRESCRIPTION_DOCTYPE_CANDIDATES:
        fields = _doctype_fields(doctype)
        if not fields:
            continue

        dispensed_field = next(
            (
                candidate
                for candidate in ("dispensed", "is_dispensed", "prescription_dispensed")
                if candidate in fields
            ),
            None,
        )
        status_field = next(
            (candidate for candidate in ("dispense_status", "status", "workflow_state") if candidate in fields),
            None,
        )
        if not dispensed_field and not status_field:
            continue

        conditions = []
        values = {}
        if dispensed_field:
            conditions.append(_truthy_sql("p.`{0}`".format(dispensed_field)))
        else:
            placeholders = []
            for index, status in enumerate(DISPENSED_STATUS_VALUES):
                key = "dispensed_status_{0}".format(index)
                values[key] = status
                placeholders.append("%({0})s".format(key))
            conditions.append(
                "LOWER(COALESCE(CAST(p.`{0}` AS CHAR), '')) IN ({1})".format(
                    status_field, ", ".join(placeholders)
                )
            )

        if "docstatus" in fields:
            conditions.append("p.docstatus != 2")
        if filters.get("company") and "company" in fields:
            conditions.append("p.`company` = %(prescription_company)s")
            values["prescription_company"] = filters.company

        date_field = next(
            (
                candidate
                for candidate in (
                    "dispensed_on",
                    "dispensed_date",
                    "posting_date",
                    "prescription_date",
                    "date",
                    "creation",
                )
                if candidate in fields
            ),
            None,
        )
        if date_field:
            _append_resolved_date_conditions(
                conditions,
                values,
                "p.`{0}`".format(date_field),
                filters,
                "prescription",
                datetime_field=_doctype_fieldtype(doctype, date_field) in {"Datetime", "Time"}
                or date_field in {"creation", "modified"},
            )
        else:
            warnings.append(
                _("{0} has no resolved date field; dispensed prescriptions are not period-filtered.").format(doctype)
            )

        count = frappe.db.sql(
            "SELECT COUNT(DISTINCT p.name) FROM `tab{0}` p WHERE {1}".format(
                doctype, " AND ".join(conditions)
            ),
            values,
        )[0][0]
        return cint(count), doctype, warnings

    warnings.append(
        _("No supported prescription DocType with a dispensed flag/status was resolved; Prescriptions Dispensed is unavailable.")
    )
    return None, None, warnings


def _get_stock_operations(filters):
    # Warehouse report: only enabled warehouses that have one of the explicit
    # site mappings (Drug, Optical, or Supermarket).
    warehouse_rows = _enabled_warehouse_rows(filters)
    warehouse = _stock_exception_source(filters, warehouse_rows, "Warehouse")

    if warehouse.get("available"):
        pending_count, pending_rows, po_warning = _get_pending_purchase_orders(
            filters, warehouse.get("warehouse_names") or [], "Warehouse"
        )
        warehouse["totals"]["pending_pos"] = pending_count
        warehouse["pending_pos"] = pending_rows
        if po_warning:
            warehouse.setdefault("warnings", []).append(po_warning)
        if pending_count and warehouse.get("status") == "GREEN":
            warehouse["status"] = "AMBER"

    # Pharmacy report: enabled pharmacy warehouses paired only with Item Group Drug.
    pharmacy_rows = _enabled_warehouse_rows(filters, required_item_groups=("Drug",))
    pharmacy = _stock_exception_source(filters, pharmacy_rows, "Pharmacy")
    if pharmacy.get("available"):
        pending_count, pending_rows, po_warning = _get_pending_purchase_orders(
            filters, pharmacy.get("warehouse_names") or [], "Pharmacy"
        )
        dispensed, prescription_source, prescription_warnings = _get_dispensed_prescriptions(filters)
        pharmacy["totals"]["pending_pos"] = pending_count
        pharmacy["totals"]["prescriptions_dispensed"] = dispensed
        pharmacy["pending_pos"] = pending_rows
        pharmacy["prescription_source"] = prescription_source
        pharmacy.setdefault("warnings", []).extend(prescription_warnings)
        if po_warning:
            pharmacy["warnings"].append(po_warning)
        if pending_count and pharmacy.get("status") == "GREEN":
            pharmacy["status"] = "AMBER"
        pharmacy["warnings"].append(
            _("Pharmacy reads enabled Pharmacy warehouses and Item Group Drug only.")
        )

    return {
        "available": warehouse.get("available") or pharmacy.get("available"),
        "label": "Stock Operations",
        "status": max(
            (warehouse.get("status", "GREY"), pharmacy.get("status", "GREY")),
            key=_status_rank,
        ),
        "warehouse": warehouse,
        "pharmacy": pharmacy,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Optical Department — Que, EyeGlass Prescription, sales and stock exceptions
# ---------------------------------------------------------------------------


def _get_optical(filters):
    warnings = []
    totals = {
        "eye_patients_seen": 0,
        "glasses_prescribed_patients": None,
        "glasses_prescriptions": None,
        "glasses_sold": 0.0,
        "frames_sold": 0.0,
        "pending_glasses_orders": 0,
        "pending_glasses_qty": 0.0,
        "frame_stockouts": 0,
    }
    trend = []
    recent_prescriptions = []
    frame_stockout_rows = []

    # 1) Exact business rule: tabQue.department = 'Opthalmology'.
    que_fields = _doctype_fields("Que")
    if que_fields and "date" in que_fields and "department" in que_fields:
        values = {"optical_department_exact": OPTICAL_MEDICAL_DEPARTMENT}
        conditions = [
            "LOWER(TRIM(q.`department`)) = LOWER(%(optical_department_exact)s)"
        ]
        _append_resolved_date_conditions(
            conditions,
            values,
            "q.`date`",
            filters,
            "optical_queue",
            datetime_field=False,
        )
        if filters.get("practitioner") and "practitioner" in que_fields:
            conditions.append("q.`practitioner` = %(optical_practitioner)s")
            values["optical_practitioner"] = filters.practitioner

        patient_expr = "q.`patient`" if "patient" in que_fields else "q.name"
        eye_rows = frappe.db.sql(
            """
            SELECT COUNT(DISTINCT {patient}) AS patients
            FROM `tabQue` q
            WHERE {where_clause}
            """.format(patient=patient_expr, where_clause=" AND ".join(conditions)),
            values,
            as_dict=True,
        )
        totals["eye_patients_seen"] = cint(eye_rows[0].get("patients")) if eye_rows else 0

        trend = frappe.db.sql(
            """
            SELECT q.`date` AS period, COUNT(DISTINCT {patient}) AS patients
            FROM `tabQue` q
            WHERE {where_clause}
            GROUP BY q.`date`
            ORDER BY q.`date` ASC
            """.format(patient=patient_expr, where_clause=" AND ".join(conditions)),
            values,
            as_dict=True,
        )
        trend = _normalise_sql_rows(trend, integer_fields=("patients",))
        warnings.append(
            _("Eye Patients Seen reads distinct Que patients where department equals Opthalmology.")
        )
    else:
        warnings.append(_("Que.date or Que.department was not found; Eye Patients Seen is unavailable."))
        totals["eye_patients_seen"] = None

    # 2) Include draft and submitted prescriptions; exclude cancelled only.
    prescription_fields = _doctype_fields("EyeGlass Prescription")
    if prescription_fields:
        values = {}
        conditions = ["eg.docstatus != 2"]
        warnings.append(_("EyeGlass Prescription uses docstatus != 2 (cancelled documents excluded)."))
        if filters.get("company") and "company" in prescription_fields:
            conditions.append("eg.`company` = %(eyeglass_company)s")
            values["eyeglass_company"] = filters.company

        date_field = next(
            (
                candidate
                for candidate in ("date", "posting_date", "prescription_date", "creation")
                if candidate in prescription_fields
            ),
            None,
        )
        if date_field:
            _append_resolved_date_conditions(
                conditions,
                values,
                "eg.`{0}`".format(date_field),
                filters,
                "eyeglass",
                datetime_field=_doctype_fieldtype("EyeGlass Prescription", date_field) == "Datetime"
                or date_field == "creation",
            )
        else:
            warnings.append(_("EyeGlass Prescription has no resolved date field; prescription counts are not period-filtered."))

        patient_field = next(
            (candidate for candidate in ("patient", "patient_id") if candidate in prescription_fields),
            None,
        )
        patient_name_field = next(
            (
                candidate
                for candidate in ("patient_name", "patient_full_name", "customer_name")
                if candidate in prescription_fields
            ),
            None,
        )
        count_expr = "COUNT(DISTINCT eg.`{0}`)".format(patient_field) if patient_field else "COUNT(*)"
        count_rows = frappe.db.sql(
            """
            SELECT {patient_count} AS patients, COUNT(*) AS documents
            FROM `tabEyeGlass Prescription` eg
            WHERE {where_clause}
            """.format(patient_count=count_expr, where_clause=" AND ".join(conditions)),
            values,
            as_dict=True,
        )
        if count_rows:
            totals["glasses_prescribed_patients"] = cint(count_rows[0].get("patients"))
            totals["glasses_prescriptions"] = cint(count_rows[0].get("documents"))

        date_expr = "eg.`{0}`".format(date_field) if date_field else "eg.creation"
        patient_expr = "eg.`{0}`".format(patient_field) if patient_field else "NULL"
        patient_name_expr = "eg.`{0}`".format(patient_name_field) if patient_name_field else "NULL"
        recent_prescriptions = frappe.db.sql(
            """
            SELECT
                eg.name,
                {date_expr} AS prescription_date,
                {patient_expr} AS patient,
                {patient_name_expr} AS patient_name,
                eg.docstatus
            FROM `tabEyeGlass Prescription` eg
            WHERE {where_clause}
            ORDER BY {date_expr} DESC, eg.modified DESC
            LIMIT 20
            """.format(
                date_expr=date_expr,
                patient_expr=patient_expr,
                patient_name_expr=patient_name_expr,
                where_clause=" AND ".join(conditions),
            ),
            values,
            as_dict=True,
        )
    else:
        warnings.append(_("DocType EyeGlass Prescription was not found."))

    # Optical sales: prefer exact Item Group Optical, then use keywords to split
    # glasses/lenses from frames.
    sii_fields = _doctype_fields("Sales Invoice Item")
    si_fields = _doctype_fields("Sales Invoice")
    if sii_fields and si_fields and {"parent", "item_code", "qty"}.issubset(sii_fields):
        values = {}
        conditions = ["si.docstatus = 1", "sii.`parenttype` = 'Sales Invoice'"]
        _append_resolved_date_conditions(
            conditions,
            values,
            "si.`posting_date`",
            filters,
            "optical_sales",
            datetime_field=False,
        )
        if filters.get("company") and "company" in si_fields:
            conditions.append("si.`company` = %(optical_sales_company)s")
            values["optical_sales_company"] = filters.company
        if "item_group" in sii_fields:
            conditions.append("LOWER(TRIM(sii.`item_group`)) = 'optical'")

        item_expressions = ["sii.`item_code`"]
        for fieldname in ("item_name", "item_group", "description"):
            if fieldname in sii_fields:
                item_expressions.append("sii.`{0}`".format(fieldname))
        frame_condition = _keyword_condition(
            item_expressions,
            FRAME_ITEM_KEYWORDS,
            "optical_frame_sale",
            values,
        )
        optical_condition = _keyword_condition(
            item_expressions,
            OPTICAL_ITEM_KEYWORDS,
            "optical_glasses_sale",
            values,
        )
        sales_rows = frappe.db.sql(
            """
            SELECT
                COALESCE(SUM(CASE WHEN ({glasses}) AND NOT ({frames}) THEN sii.`qty` ELSE 0 END), 0) AS glasses_sold,
                COALESCE(SUM(CASE WHEN {frames} THEN sii.`qty` ELSE 0 END), 0) AS frames_sold
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si
                ON si.name = sii.`parent`
            WHERE {where_clause}
            """.format(
                glasses=optical_condition,
                frames=frame_condition,
                where_clause=" AND ".join(conditions),
            ),
            values,
            as_dict=True,
        )
        if sales_rows:
            totals["glasses_sold"] = round(flt(sales_rows[0].get("glasses_sold")), 2)
            totals["frames_sold"] = round(flt(sales_rows[0].get("frames_sold")), 2)
    else:
        warnings.append(_("Sales Invoice Item fields were insufficient to calculate Glasses Sold and Frames Sold."))
        totals["glasses_sold"] = None
        totals["frames_sold"] = None

    soi_fields = _doctype_fields("Sales Order Item")
    so_fields = _doctype_fields("Sales Order")
    if soi_fields and so_fields and {"parent", "item_code", "qty"}.issubset(soi_fields):
        values = {}
        conditions = ["so.docstatus = 1", "soi.`parenttype` = 'Sales Order'"]
        if "status" in so_fields:
            conditions.append("COALESCE(so.`status`, '') NOT IN ('Completed', 'Closed')")
        _append_resolved_date_conditions(
            conditions,
            values,
            "so.`transaction_date`" if "transaction_date" in so_fields else "so.creation",
            filters,
            "optical_order",
            datetime_field="transaction_date" not in so_fields,
        )
        if filters.get("company") and "company" in so_fields:
            conditions.append("so.`company` = %(optical_order_company)s")
            values["optical_order_company"] = filters.company
        if "item_group" in soi_fields:
            conditions.append("LOWER(TRIM(soi.`item_group`)) = 'optical'")
        item_expressions = ["soi.`item_code`"]
        for fieldname in ("item_name", "item_group", "description"):
            if fieldname in soi_fields:
                item_expressions.append("soi.`{0}`".format(fieldname))
        conditions.append(
            _keyword_condition(
                item_expressions,
                OPTICAL_ITEM_KEYWORDS + FRAME_ITEM_KEYWORDS,
                "optical_pending_order",
                values,
            )
        )
        delivered_expr = "COALESCE(soi.`delivered_qty`, 0)" if "delivered_qty" in soi_fields else "0"
        order_rows = frappe.db.sql(
            """
            SELECT
                COUNT(DISTINCT so.name) AS orders,
                COALESCE(SUM(GREATEST(COALESCE(soi.`qty`, 0) - {delivered}, 0)), 0) AS pending_qty
            FROM `tabSales Order Item` soi
            INNER JOIN `tabSales Order` so
                ON so.name = soi.`parent`
            WHERE {where_clause}
            """.format(delivered=delivered_expr, where_clause=" AND ".join(conditions)),
            values,
            as_dict=True,
        )
        if order_rows:
            totals["pending_glasses_orders"] = cint(order_rows[0].get("orders"))
            totals["pending_glasses_qty"] = round(flt(order_rows[0].get("pending_qty")), 2)
    else:
        warnings.append(_("Sales Order Item fields were insufficient to calculate Pending Glasses Orders."))
        totals["pending_glasses_orders"] = None
        totals["pending_glasses_qty"] = None

    # Frame stockouts are restricted to enabled Optical warehouses, Item Group
    # Optical, and frame-named items. Expired items count as unavailable.
    optical_warehouses = _enabled_warehouse_rows(filters, required_item_groups=("Optical",))
    warehouse_names = [row.get("name") for row in optical_warehouses if row.get("name")]
    bin_fields = _doctype_fields("Bin")
    item_fields = _doctype_fields("Item")
    if (
        warehouse_names
        and bin_fields
        and item_fields
        and {"item_code", "warehouse", "actual_qty"}.issubset(bin_fields)
        and "item_group" in item_fields
    ):
        values = {"frame_warehouses": tuple(warehouse_names), "frame_today": now_datetime().date().isoformat()}
        item_expressions = ["i.`item_code`"]
        for fieldname in ("item_name", "description"):
            if fieldname in item_fields:
                item_expressions.append("i.`{0}`".format(fieldname))
        frame_condition = _keyword_condition(
            item_expressions,
            FRAME_ITEM_KEYWORDS,
            "frame_stockout",
            values,
        )
        name_expr = (
            "COALESCE(NULLIF(TRIM(i.`item_name`), ''), i.`item_code`)"
            if "item_name" in item_fields
            else "i.`item_code`"
        )
        expiry_field = next(
            (candidate for candidate in ITEM_EXPIRY_FIELD_CANDIDATES if candidate in item_fields),
            None,
        )
        expiry_expr = "NULL"
        if expiry_field:
            expiry_expr = """
                CASE
                    WHEN CAST(i.`{field}` AS CHAR) REGEXP '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                     AND CAST(i.`{field}` AS CHAR) <> '0000-00-00'
                    THEN DATE(i.`{field}`)
                    ELSE NULL
                END
            """.format(field=expiry_field).strip()
        stockout_condition = "COALESCE(b.`actual_qty`, 0) <= 0"
        if expiry_field:
            stockout_condition = "(COALESCE(b.`actual_qty`, 0) <= 0 OR ({expiry} IS NOT NULL AND {expiry} < %(frame_today)s))".format(
                expiry=expiry_expr
            )

        frame_stockout_rows = frappe.db.sql(
            """
            SELECT
                b.`warehouse` AS warehouse,
                i.`item_code` AS item_code,
                {name_expr} AS item_name,
                COALESCE(b.`actual_qty`, 0) AS actual_qty,
                {expiry_expr} AS expiry_date,
                CASE
                    WHEN {expiry_expr} IS NOT NULL AND {expiry_expr} < %(frame_today)s THEN 'Expired'
                    ELSE 'Zero / Negative Qty'
                END AS stockout_reason
            FROM `tabBin` b
            INNER JOIN `tabItem` i ON i.`item_code` = b.`item_code`
            INNER JOIN `tabWarehouse` w ON w.name = b.`warehouse`
            WHERE b.`warehouse` IN %(frame_warehouses)s
              AND LOWER(TRIM(i.`item_group`)) = 'optical'
              AND ({frame_condition})
              AND ({stockout_condition})
              AND COALESCE(w.`disabled`, 0) = 0
              AND COALESCE(w.`is_group`, 0) = 0
            ORDER BY i.`item_code` ASC
            LIMIT 40
            """.format(
                name_expr=name_expr,
                expiry_expr=expiry_expr,
                frame_condition=frame_condition,
                stockout_condition=stockout_condition,
            ),
            values,
            as_dict=True,
        )
        frame_stockout_rows = _normalise_sql_rows(frame_stockout_rows, float_fields=("actual_qty",))
        totals["frame_stockouts"] = len({row.get("item_code") for row in frame_stockout_rows if row.get("item_code")})
    else:
        warnings.append(_("Frame Stockouts require an enabled Optical warehouse, Item Group Optical, and Bin stock data."))
        totals["frame_stockouts"] = None

    if (totals.get("frame_stockouts") or 0) > 0:
        status = "RED"
    elif (totals.get("pending_glasses_orders") or 0) > 0:
        status = "AMBER"
    elif any(value not in (None, 0, 0.0) for value in totals.values()):
        status = "GREEN"
    else:
        status = "GREY"

    return {
        "available": True,
        "label": "Optical",
        "status": status,
        "totals": totals,
        "trend": trend,
        "recent_prescriptions": recent_prescriptions,
        "frame_stockouts": frame_stockout_rows,
        "report": None,
        "report_exists": False,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# HR Attendance — Employee Checkin status on Queue Date
# ---------------------------------------------------------------------------


def _clock_seconds(value):
    """Convert a Time/timedelta/string value to seconds after midnight."""
    if value in (None, ""):
        return None
    if isinstance(value, timedelta):
        return int(value.total_seconds()) % 86400
    if isinstance(value, datetime_type):
        return value.hour * 3600 + value.minute * 60 + value.second
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return int(value.hour) * 3600 + int(value.minute) * 60 + int(getattr(value, "second", 0) or 0)

    text = str(value).strip()
    if " " in text:
        text = text.split(" ")[-1]
    try:
        parts = [int(float(part)) for part in text.split(":")]
    except (TypeError, ValueError):
        return None
    while len(parts) < 3:
        parts.append(0)
    return (parts[0] * 3600 + parts[1] * 60 + parts[2]) % 86400


def _attendance_shift_rules(employee_ids):
    """Bulk-load custom Employee shift slots used by the supplied attendance report."""
    rules = defaultdict(lambda: {"has_normal": False, "overnight": []})
    if not employee_ids:
        return rules

    employee_meta = frappe.get_meta("Employee")
    slot_field = employee_meta.get_field("shift_type_slot")
    if not slot_field or not slot_field.options or not frappe.db.exists("DocType", slot_field.options):
        return rules

    slot_fields = _doctype_fields(slot_field.options)
    required = {"parent", "time_in", "time_out"}
    if not required.issubset(slot_fields):
        return rules

    fields = ["parent", "time_in", "time_out"]
    if "shift_type" in slot_fields:
        fields.append("shift_type")
    slots = frappe.get_all(
        slot_field.options,
        filters={"parent": ["in", list(employee_ids)]},
        fields=fields,
        limit_page_length=0,
    )

    shift_names = {row.get("shift_type") for row in slots if row.get("shift_type")}
    shift_map = {}
    shift_fields = _doctype_fields("Shift Type")
    wanted = [
        field
        for field in (
            "name",
            "two_date_shift",
            "begin_check_in_before_shift_start_time",
            "allow_check_out_after_shift_end_time",
        )
        if field == "name" or field in shift_fields
    ]
    if shift_names and wanted:
        for row in frappe.get_all(
            "Shift Type",
            filters={"name": ["in", list(shift_names)]},
            fields=wanted,
            limit_page_length=0,
        ):
            shift_map[row.get("name")] = row

    for slot in slots:
        employee = slot.get("parent")
        time_in = _clock_seconds(slot.get("time_in"))
        time_out = _clock_seconds(slot.get("time_out"))
        if not employee or time_in is None or time_out is None:
            continue
        shift = shift_map.get(slot.get("shift_type")) or {}
        is_overnight = bool(cint(shift.get("two_date_shift"))) or time_out < time_in
        if not is_overnight:
            rules[employee]["has_normal"] = True
            rules[employee].setdefault("normal_starts", []).append(time_in)
            continue

        begin_before = cint(shift.get("begin_check_in_before_shift_start_time")) * 60
        allow_after = cint(shift.get("allow_check_out_after_shift_end_time")) * 60
        rules[employee]["overnight"].append(
            {
                "start": max(0, time_in - begin_before),
                "next_day_cutoff": min(86399, time_out + allow_after),
            }
        )

    return rules


def _attendance_holiday_employees(employee_rows, attendance_date):
    employee_fields = _doctype_fields("Employee")
    company_fields = _doctype_fields("Company")
    holiday_fields = _doctype_fields("Holiday")
    if not holiday_fields or not {"parent", "holiday_date"}.issubset(holiday_fields):
        return set(), _("Holiday data could not be resolved; no employee is excluded as Holiday.")

    company_defaults = {}
    companies = {row.get("company") for row in employee_rows if row.get("company")}
    if companies and "default_holiday_list" in company_fields:
        for row in frappe.get_all(
            "Company",
            filters={"name": ["in", list(companies)]},
            fields=["name", "default_holiday_list"],
            limit_page_length=0,
        ):
            company_defaults[row.get("name")] = row.get("default_holiday_list")

    list_by_employee = {}
    holiday_lists = set()
    for row in employee_rows:
        holiday_list = row.get("holiday_list") if "holiday_list" in employee_fields else None
        holiday_list = holiday_list or company_defaults.get(row.get("company"))
        if holiday_list:
            list_by_employee[row.get("employee")] = holiday_list
            holiday_lists.add(holiday_list)

    if not holiday_lists:
        return set(), None

    holiday_rows = frappe.get_all(
        "Holiday",
        filters={
            "parent": ["in", list(holiday_lists)],
            "holiday_date": attendance_date,
        },
        fields=["parent"],
        limit_page_length=0,
    )
    holiday_parents = {row.get("parent") for row in holiday_rows if row.get("parent")}
    return {
        employee
        for employee, holiday_list in list_by_employee.items()
        if holiday_list in holiday_parents
    }, None


def _attendance_leave_employees(employee_ids, attendance_date):
    """Support the supplied custom Leaves Assigment and standard Leave Application."""
    if not employee_ids:
        return set()

    on_leave = set()
    for doctype in ("Leaves Assigment", "Leave Application"):
        fields = _doctype_fields(doctype)
        if not fields or not {"employee", "from_date", "to_date"}.issubset(fields):
            continue
        conditions = [
            "la.`employee` IN %(leave_employees)s",
            "la.`from_date` <= %(leave_date)s",
            "la.`to_date` >= %(leave_date)s",
        ]
        values = {
            "leave_employees": tuple(employee_ids),
            "leave_date": attendance_date.isoformat(),
        }
        if "docstatus" in fields:
            conditions.append("la.docstatus = 1")
        if doctype == "Leave Application" and "status" in fields:
            conditions.append("COALESCE(la.`status`, '') IN ('Approved', 'Open')")
        rows = frappe.db.sql(
            "SELECT DISTINCT la.`employee` AS employee FROM `tab{0}` la WHERE {1}".format(
                doctype,
                " AND ".join(conditions),
            ),
            values,
            as_dict=True,
        )
        on_leave.update(row.get("employee") for row in rows if row.get("employee"))
    return on_leave


def _employee_staff_type(employee, designation, practitioner_employees):
    text = str(designation or "").lower()
    if employee in practitioner_employees or any(keyword in text for keyword in DOCTOR_DESIGNATION_KEYWORDS):
        return "Doctor"
    if any(keyword in text for keyword in NURSE_DESIGNATION_KEYWORDS):
        return "Nurse"
    return "Staff"


def _get_staffing(filters):
    """Daily HR attendance derived from Employee Checkin, not Attendance."""
    employee_fields = _doctype_fields("Employee")
    checkin_fields = _doctype_fields("Employee Checkin")
    if not employee_fields:
        return _unavailable("HR Attendance", _("DocType Employee was not found."))
    if not checkin_fields:
        return _unavailable("HR Attendance", _("DocType Employee Checkin was not found."))
    if not {"employee", "time"}.issubset(checkin_fields):
        return _unavailable(
            "HR Attendance",
            _("Employee Checkin is missing employee or time."),
        )

    attendance_date = _safe_getdate(filters.get("queue_date") or filters.get("to_date"))
    if not attendance_date:
        return _unavailable("HR Attendance", _("A valid attendance date is required."))

    warnings = []
    conditions = []
    values = {}
    if "status" in employee_fields:
        conditions.append("e.`status` = 'Active'")
    if "date_of_joining" in employee_fields:
        conditions.append("(e.`date_of_joining` IS NULL OR e.`date_of_joining` <= %(attendance_date)s)")
        values["attendance_date"] = attendance_date.isoformat()
    if filters.get("company") and "company" in employee_fields:
        conditions.append("e.`company` = %(attendance_company)s")
        values["attendance_company"] = filters.company
    if filters.get("hr_department") and "department" in employee_fields:
        conditions.append("e.`department` = %(attendance_department)s")
        values["attendance_department"] = filters.hr_department
    if filters.get("designation") and "designation" in employee_fields:
        conditions.append("e.`designation` = %(attendance_designation)s")
        values["attendance_designation"] = filters.designation
    if filters.get("employment_type") and "employment_type" in employee_fields:
        conditions.append("e.`employment_type` = %(attendance_employment_type)s")
        values["attendance_employment_type"] = filters.employment_type

    def employee_expr(fieldname, fallback="NULL"):
        return "e.`{0}`".format(fieldname) if fieldname in employee_fields else fallback

    employee_name_expr = (
        "COALESCE(NULLIF(TRIM(e.`employee_name`), ''), e.name)"
        if "employee_name" in employee_fields
        else "e.name"
    )
    employee_rows = frappe.db.sql(
        """
        SELECT
            e.name AS employee,
            {employee_name} AS employee_name,
            {company} AS company,
            {department} AS department,
            {designation} AS designation,
            {holiday_list} AS holiday_list
        FROM `tabEmployee` e
        {where_sql}
        ORDER BY employee_name ASC, e.name ASC
        """.format(
            employee_name=employee_name_expr,
            company=employee_expr("company"),
            department=employee_expr("department"),
            designation=employee_expr("designation"),
            holiday_list=employee_expr("holiday_list"),
            where_sql=("WHERE " + " AND ".join(conditions)) if conditions else "",
        ),
        values,
        as_dict=True,
    )
    employee_rows = [frappe._dict(row) for row in employee_rows]
    employee_ids = [row.employee for row in employee_rows if row.get("employee")]

    if not employee_ids:
        return {
            "available": True,
            "label": "HR Attendance",
            "status": "GREY",
            "attendance_date": attendance_date.isoformat(),
            "totals": {
                "total_staff": 0,
                "present_staff": 0,
                "absent_staff": 0,
                "not_checked_in": 0,
                "on_leave": 0,
                "holiday": 0,
                "absent_doctors": 0,
                "absent_nurses": 0,
                "departments_affected": 0,
                "attendance_rate": None,
            },
            "by_department": [],
            "attendance_exceptions": [],
            "report": ATTENDANCE_REPORT,
            "report_exists": _report_exists(ATTENDANCE_REPORT),
            "warnings": [],
        }

    start_datetime = "{0} 00:00:00".format(attendance_date.isoformat())
    end_date = attendance_date + timedelta(days=2)
    end_datetime = "{0} 00:00:00".format(end_date.isoformat())
    checkin_rows = frappe.db.sql(
        """
        SELECT ec.`employee` AS employee, ec.`time` AS checkin_time
        FROM `tabEmployee Checkin` ec
        WHERE ec.`employee` IN %(attendance_employee_ids)s
          AND ec.`time` >= %(attendance_start)s
          AND ec.`time` < %(attendance_end)s
        ORDER BY ec.`employee`, ec.`time`
        """,
        {
            "attendance_employee_ids": tuple(employee_ids),
            "attendance_start": start_datetime,
            "attendance_end": end_datetime,
        },
        as_dict=True,
    )
    checkins = defaultdict(list)
    for row in checkin_rows:
        try:
            stamp = get_datetime(row.get("checkin_time"))
        except Exception:
            continue
        checkins[row.get("employee")].append(stamp)

    shift_rules = _attendance_shift_rules(employee_ids)
    holiday_employees, holiday_warning = _attendance_holiday_employees(
        employee_rows,
        attendance_date,
    )
    if holiday_warning:
        warnings.append(holiday_warning)
    leave_employees = _attendance_leave_employees(employee_ids, attendance_date)

    practitioner_employees = set()
    practitioner_fields = _doctype_fields("Healthcare Practitioner")
    if practitioner_fields and "employee" in practitioner_fields:
        practitioner_employees = set(
            frappe.get_all(
                "Healthcare Practitioner",
                filters={"employee": ["in", employee_ids]},
                pluck="employee",
                limit_page_length=0,
            )
        )

    today = now_datetime().date()
    now_seconds = (
        now_datetime().hour * 3600
        + now_datetime().minute * 60
        + now_datetime().second
    )
    by_department_map = defaultdict(
        lambda: {
            "total_staff": 0,
            "present_staff": 0,
            "absent_staff": 0,
            "not_checked_in": 0,
            "on_leave": 0,
            "holiday": 0,
            "absent_doctors": 0,
            "absent_nurses": 0,
        }
    )
    attendance_rows = []

    for employee_row in employee_rows:
        employee = employee_row.employee
        rules = shift_rules.get(employee) or {"has_normal": False, "overnight": []}
        employee_punches = checkins.get(employee) or []
        accepted = []

        for stamp in employee_punches:
            if stamp.date() == attendance_date:
                second = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
                if rules.get("overnight") and not rules.get("has_normal"):
                    if any(second >= rule["start"] for rule in rules["overnight"]):
                        accepted.append(stamp)
                else:
                    accepted.append(stamp)
            elif stamp.date() == attendance_date + timedelta(days=1):
                second = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
                if any(second <= rule["next_day_cutoff"] for rule in rules.get("overnight") or []):
                    accepted.append(stamp)

        accepted.sort()
        on_leave = employee in leave_employees
        on_holiday = employee in holiday_employees
        if accepted:
            status = "Present"
        elif on_leave:
            status = "On Leave"
        elif on_holiday:
            status = "Holiday"
        elif attendance_date < today:
            status = "Absent"
        elif attendance_date > today:
            status = "Not Due"
        else:
            normal_starts = rules.get("normal_starts") or []
            overnight_starts = [rule["start"] for rule in rules.get("overnight") or []]
            expected_starts = normal_starts + overnight_starts
            if expected_starts and now_seconds >= min(expected_starts):
                status = "Absent"
            else:
                status = "Not Checked In"

        staff_type = _employee_staff_type(
            employee,
            employee_row.get("designation"),
            practitioner_employees,
        )
        department = employee_row.get("department") or _("Unassigned")
        bucket = by_department_map[department]
        bucket["total_staff"] += 1
        if status == "Present":
            bucket["present_staff"] += 1
        elif status == "Absent":
            bucket["absent_staff"] += 1
            if staff_type == "Doctor":
                bucket["absent_doctors"] += 1
            elif staff_type == "Nurse":
                bucket["absent_nurses"] += 1
        elif status == "On Leave":
            bucket["on_leave"] += 1
        elif status == "Holiday":
            bucket["holiday"] += 1
        else:
            bucket["not_checked_in"] += 1

        attendance_rows.append(
            {
                "employee": employee,
                "employee_name": employee_row.get("employee_name") or employee,
                "department": department,
                "designation": employee_row.get("designation") or "",
                "staff_type": staff_type,
                "status": status,
                "first_checkin": accepted[0] if accepted else None,
                "last_checkin": accepted[-1] if accepted else None,
                "punches": len(accepted),
            }
        )

    by_department = []
    for department, values_ in by_department_map.items():
        row = {"department": department}
        row.update(values_)
        by_department.append(row)
    by_department.sort(
        key=lambda row: (
            row.get("absent_staff", 0),
            row.get("not_checked_in", 0),
            row.get("total_staff", 0),
        ),
        reverse=True,
    )

    totals = {
        key: sum(cint(row.get(key)) for row in by_department)
        for key in (
            "total_staff",
            "present_staff",
            "absent_staff",
            "not_checked_in",
            "on_leave",
            "holiday",
            "absent_doctors",
            "absent_nurses",
        )
    }
    totals["departments_affected"] = sum(
        1 for row in by_department if cint(row.get("absent_staff")) > 0
    )
    expected = totals["present_staff"] + totals["absent_staff"] + totals["not_checked_in"]
    totals["attendance_rate"] = (
        round(totals["present_staff"] / expected * 100, 1) if expected else None
    )

    if totals["absent_staff"]:
        status = "RED"
    elif totals["not_checked_in"]:
        status = "AMBER"
    else:
        status = "GREEN" if totals["total_staff"] else "GREY"

    attendance_exceptions = [
        row
        for row in attendance_rows
        if row.get("status") in {"Absent", "Not Checked In", "Not Due"}
    ]
    attendance_exceptions.sort(
        key=lambda row: (
            0 if row.get("status") == "Absent" else 1,
            row.get("department") or "",
            row.get("employee_name") or "",
        )
    )

    warnings.extend(
        [
            _("Attendance is derived from Employee Checkin on the selected Queue Date and active employees only."),
            _("Any accepted check-in is classified as Present; Half Day is not used."),
            _("For today, an employee becomes Absent only after the configured shift start; otherwise the status is Not Checked In."),
        ]
    )

    return {
        "available": True,
        "label": "HR Attendance",
        "status": status,
        "attendance_date": attendance_date.isoformat(),
        "totals": totals,
        "by_department": by_department,
        "attendance_rows": attendance_rows,
        "attendance_exceptions": attendance_exceptions[:150],
        # Compatibility for existing export/render helpers.
        "absent_employees": [row for row in attendance_rows if row.get("status") == "Absent"][:150],
        "report": ATTENDANCE_REPORT,
        "report_exists": _report_exists(ATTENDANCE_REPORT),
        "warnings": warnings,
    }



def _get_hr(filters):
    doctype = "Employee"
    fields = _doctype_fields(doctype)
    if not fields:
        return _unavailable("HR", _("DocType Employee was not found."))

    warnings = []
    conditions = ["1=1"]
    values = {"hr_as_of_date": filters.get("to_date") or now_datetime().date()}

    filter_map = (
        ("company", "company"),
        ("hr_department", "department"),
        ("designation", "designation"),
        ("employment_type", "employment_type"),
        ("employee_status", "status"),
    )
    for filter_name, fieldname in filter_map:
        value = filters.get(filter_name)
        if not value:
            continue
        if fieldname in fields:
            conditions.append("e.`{0}` = %({1})s".format(fieldname, filter_name))
            values[filter_name] = value
        else:
            warnings.append(
                _("The {0} filter could not be applied because Employee.{1} was not found.").format(
                    filter_name.replace("_", " ").title(), fieldname
                )
            )

    def employee_expr(fieldname, fallback="NULL"):
        if fieldname in fields:
            return "e.`{0}`".format(fieldname)
        return fallback

    employee_name_expr = (
        "COALESCE(NULLIF(TRIM(e.`employee_name`), ''), e.name)"
        if "employee_name" in fields
        else "e.name"
    )

    mobile_expr = "NULL"
    for candidate in ("cell_number", "personal_mobile_no", "mobile_no"):
        if candidate in fields:
            mobile_expr = "NULLIF(TRIM(e.`{0}`), '')".format(candidate)
            break

    ssa_fields = _doctype_fields("Salary Structure Assignment")
    salary_join = ""
    base_salary_expr = "0"
    salary_structure_expr = "NULL"
    can_join_salary = bool(
        ssa_fields
        and {"employee", "from_date", "base"}.issubset(ssa_fields)
    )

    if can_join_salary:
        company_match = ""
        if "company" in ssa_fields and "company" in fields:
            company_match = " AND ssa2.`company` = e.`company`"

        salary_join = """
        LEFT JOIN `tabSalary Structure Assignment` ssa
            ON ssa.name = (
                SELECT ssa2.name
                FROM `tabSalary Structure Assignment` ssa2
                WHERE ssa2.`employee` = e.name
                  AND ssa2.docstatus = 1
                  AND ssa2.`from_date` <= %(hr_as_of_date)s
                  {company_match}
                ORDER BY ssa2.`from_date` DESC, ssa2.modified DESC, ssa2.name DESC
                LIMIT 1
            )
        """.format(company_match=company_match)
        base_salary_expr = "COALESCE(ssa.`base`, 0)"
        if "salary_structure" in ssa_fields:
            salary_structure_expr = "ssa.`salary_structure`"
    else:
        warnings.append(
            _("Salary Structure Assignment could not be resolved; salary cards show zero.")
        )

    if not filters.get("company"):
        warnings.append(
            _("No Company filter is selected; salary totals may combine employees from different company currencies.")
        )

    rows = frappe.db.sql(
        """
        SELECT
            e.name AS employee,
            {employee_name_expr} AS employee_name,
            {company_expr} AS company,
            {department_expr} AS department,
            {designation_expr} AS designation,
            {employment_type_expr} AS employment_type,
            {branch_expr} AS branch,
            {status_expr} AS status,
            {joining_expr} AS date_of_joining,
            {gender_expr} AS gender,
            {mobile_expr} AS mobile_no,
            {base_salary_expr} AS base_salary,
            {salary_structure_expr} AS salary_structure
        FROM `tabEmployee` e
        {salary_join}
        WHERE {where_clause}
        ORDER BY employee_name ASC, e.name ASC
        """.format(
            employee_name_expr=employee_name_expr,
            company_expr=employee_expr("company"),
            department_expr=employee_expr("department"),
            designation_expr=employee_expr("designation"),
            employment_type_expr=employee_expr("employment_type"),
            branch_expr=employee_expr("branch"),
            status_expr=employee_expr("status"),
            joining_expr=employee_expr("date_of_joining"),
            gender_expr=employee_expr("gender"),
            mobile_expr=mobile_expr,
            base_salary_expr=base_salary_expr,
            salary_structure_expr=salary_structure_expr,
            salary_join=salary_join,
            where_clause=" AND ".join(conditions),
        ),
        values,
        as_dict=True,
    )

    rows = _normalise_sql_rows(rows, float_fields=("base_salary",))
    currency = None
    if filters.get("company"):
        currency = frappe.db.get_value("Company", filters.company, "default_currency")
    currency = currency or frappe.defaults.get_global_default("currency") or ""

    status_counter = Counter((row.get("status") or _("Not Set")) for row in rows)
    gender_counter = Counter((row.get("gender") or _("Not Set")) for row in rows)
    employment_counter = Counter(
        (row.get("employment_type") or _("Not Set")) for row in rows
    )

    department_map = defaultdict(
        lambda: {
            "employees": 0,
            "active": 0,
            "inactive": 0,
            "salary_cost": 0.0,
        }
    )
    for row in rows:
        department = row.get("department") or _("Unassigned")
        bucket = department_map[department]
        bucket["employees"] += 1
        bucket["salary_cost"] += flt(row.get("base_salary"))
        if row.get("status") == "Active":
            bucket["active"] += 1
        else:
            bucket["inactive"] += 1

    by_department = [
        {
            "department": department,
            "employees": values_["employees"],
            "active": values_["active"],
            "inactive": values_["inactive"],
            "salary_cost": round(values_["salary_cost"], 2),
        }
        for department, values_ in department_map.items()
    ]
    by_department.sort(key=lambda row: row["employees"], reverse=True)

    total_salary_cost = sum(flt(row.get("base_salary")) for row in rows)
    total_employees = len(rows)
    active = cint(status_counter.get("Active"))
    inactive = cint(status_counter.get("Inactive"))
    suspended = cint(status_counter.get("Suspended"))
    left = cint(status_counter.get("Left"))

    recent = sorted(
        [row for row in rows if row.get("date_of_joining")],
        key=lambda row: str(row.get("date_of_joining")),
        reverse=True,
    )[:15]

    return {
        "available": True,
        "label": "HR",
        "status": "GREEN" if rows else "GREY",
        "currency": currency,
        "totals": {
            "employees": total_employees,
            "active": active,
            "inactive": inactive,
            "suspended": suspended,
            "left": left,
            "departments": len(department_map),
            "total_salary_cost": round(total_salary_cost, 2),
            "average_salary": round(
                total_salary_cost / total_employees if total_employees else 0, 2
            ),
        },
        "by_department": by_department[:15],
        "status_rows": [
            {"label": label, "value": count}
            for label, count in status_counter.most_common()
        ],
        "gender_rows": [
            {"label": label, "value": count}
            for label, count in gender_counter.most_common()
        ],
        "employment_type_rows": [
            {"label": label, "value": count}
            for label, count in employment_counter.most_common()
        ],
        "recent": recent,
        "report": HR_REPORT,
        "report_exists": _report_exists(HR_REPORT),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Inventory — stock grouped by warehouse from Bin, Item and Warehouse
# ---------------------------------------------------------------------------


def _get_inventory_warehouse_list(filters):
    """Resolve a selected warehouse to enabled leaf warehouses only."""
    warehouse = filters.get("warehouse")
    if not warehouse:
        return None

    warehouse_fields = _doctype_fields("Warehouse")
    if not warehouse_fields:
        return [warehouse]

    lookup_fields = [
        fieldname
        for fieldname in ("is_group", "lft", "rgt", "company", "disabled")
        if fieldname in warehouse_fields
    ]
    node = frappe.db.get_value("Warehouse", warehouse, lookup_fields, as_dict=True)
    if not node:
        return ["__no_warehouse__"]

    # A disabled warehouse must never leak into the dashboard even when a stale
    # browser filter still contains its name.
    if "disabled" in warehouse_fields and cint(node.get("disabled")):
        return ["__no_warehouse__"]

    if cint(node.get("is_group")) and node.get("lft") is not None and node.get("rgt") is not None:
        child_filters = {
            "lft": [">=", node.lft],
            "rgt": ["<=", node.rgt],
            "is_group": 0,
        }
        if "disabled" in warehouse_fields:
            child_filters["disabled"] = 0
        if filters.get("company") and "company" in warehouse_fields:
            child_filters["company"] = filters.company
        children = frappe.get_all(
            "Warehouse",
            filters=child_filters,
            pluck="name",
            limit_page_length=0,
        )
        return children or ["__no_warehouse__"]

    return [warehouse]


def _get_inventory(filters):
    """Return warehouse-level stock using SQL aggregation, not full Bin rows."""
    bin_fields = _doctype_fields("Bin")
    item_fields = _doctype_fields("Item")
    warehouse_fields = _doctype_fields("Warehouse")

    if not bin_fields:
        return _unavailable("Inventory", _("DocType Bin was not found."))
    if not item_fields:
        return _unavailable("Inventory", _("DocType Item was not found."))
    if not warehouse_fields:
        return _unavailable("Inventory", _("DocType Warehouse was not found."))

    missing_bin = sorted({"item_code", "warehouse", "actual_qty"} - bin_fields)
    missing_item = sorted({"item_code", "is_stock_item"} - item_fields)
    if missing_bin or missing_item:
        missing = ["Bin.{0}".format(value) for value in missing_bin]
        missing += ["Item.{0}".format(value) for value in missing_item]
        return _unavailable(
            "Inventory",
            _("Required stock fields were not found: {0}").format(", ".join(missing)),
        )

    warnings = []
    conditions = ["i.`is_stock_item` = 1"]
    values = {"today": now_datetime().date().isoformat()}

    if "is_group" in warehouse_fields:
        conditions.append("COALESCE(w.`is_group`, 0) = 0")

    if "disabled" in warehouse_fields:
        conditions.append("COALESCE(w.`disabled`, 0) = 0")
    else:
        warnings.append(
            _("Warehouse.disabled was not found; enabled-only filtering could not be verified.")
        )

    if filters.get("company"):
        if "company" in warehouse_fields:
            conditions.append("w.`company` = %(company)s")
            values["company"] = filters.company
        else:
            warnings.append(
                _("The Company filter could not be applied because Warehouse.company was not found.")
            )
    else:
        warnings.append(
            _("No Company filter is selected; stock value may combine warehouses from different companies.")
        )

    warehouses = _get_inventory_warehouse_list(filters)
    if warehouses:
        conditions.append("b.`warehouse` IN %(warehouse_list)s")
        values["warehouse_list"] = tuple(warehouses)

    qty_expr = "COALESCE(b.`actual_qty`, 0)"
    if "stock_value" in bin_fields:
        stock_value_expr = "COALESCE(b.`stock_value`, 0)"
    elif "valuation_rate" in bin_fields:
        stock_value_expr = "({0} * COALESCE(b.`valuation_rate`, 0))".format(qty_expr)
        warnings.append(
            _("Bin.stock_value was not found; stock value is estimated from actual quantity × valuation rate.")
        )
    else:
        stock_value_expr = "0"
        warnings.append(
            _("Neither Bin.stock_value nor Bin.valuation_rate was found; stock value is unavailable.")
        )

    standard_rate_expr = (
        "COALESCE(i.`standard_rate`, 0)" if "standard_rate" in item_fields else "0"
    )
    sales_value_expr = "(GREATEST({0}, 0) * {1})".format(
        qty_expr, standard_rate_expr
    )

    item_name_expr = (
        "COALESCE(NULLIF(TRIM(i.`item_name`), ''), i.`item_code`)"
        if "item_name" in item_fields
        else "i.`item_code`"
    )
    item_group_expr = (
        "COALESCE(NULLIF(TRIM(i.`item_group`), ''), 'Not Set')"
        if "item_group" in item_fields
        else "'Not Set'"
    )
    sub_category_expr = "i.`sub_category`" if "sub_category" in item_fields else "NULL"
    code_expr = "i.`code`" if "code" in item_fields else "NULL"

    expiry_expr = "NULL"
    if "expired_date" in item_fields:
        # Some sites store literal N/A in a custom date-like field. Validate the
        # text before DATE() so those rows are ignored instead of raising.
        expiry_expr = """
            CASE
                WHEN CAST(i.`expired_date` AS CHAR) REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                 AND CAST(i.`expired_date` AS CHAR) <> '0000-00-00'
                THEN DATE(i.`expired_date`)
                ELSE NULL
            END
        """.strip()
    else:
        warnings.append(
            _("Item.expired_date was not found; expired-item cards cannot be calculated from this report.")
        )

    from_sql = """
        FROM `tabBin` b
        INNER JOIN `tabItem` i
            ON i.`item_code` = b.`item_code`
        INNER JOIN `tabWarehouse` w
            ON w.name = b.`warehouse`
        WHERE {where_clause}
    """.format(where_clause=" AND ".join(conditions))

    summary_rows = frappe.db.sql(
        """
        SELECT
            COUNT(DISTINCT b.`item_code`) AS items,
            COUNT(*) AS warehouse_lines,
            COUNT(DISTINCT b.`warehouse`) AS warehouses,
            COALESCE(SUM({qty_expr}), 0) AS quantity,
            COALESCE(SUM({stock_value_expr}), 0) AS stock_value,
            COALESCE(SUM({sales_value_expr}), 0) AS sales_value,
            SUM(CASE WHEN {qty_expr} > 0 THEN 1 ELSE 0 END) AS positive_stock,
            SUM(CASE WHEN {qty_expr} = 0 THEN 1 ELSE 0 END) AS zero_stock,
            SUM(CASE WHEN {qty_expr} < 0 THEN 1 ELSE 0 END) AS negative_stock,
            SUM(
                CASE
                    WHEN {qty_expr} > 0
                     AND {expiry_expr} IS NOT NULL
                     AND {expiry_expr} < %(today)s
                    THEN 1 ELSE 0
                END
            ) AS expired_items
        {from_sql}
        """.format(
            qty_expr=qty_expr,
            stock_value_expr=stock_value_expr,
            sales_value_expr=sales_value_expr,
            expiry_expr=expiry_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    summary = frappe._dict(summary_rows[0] if summary_rows else {})

    by_warehouse = frappe.db.sql(
        """
        SELECT
            b.`warehouse` AS warehouse,
            COUNT(DISTINCT b.`item_code`) AS items,
            COALESCE(SUM({qty_expr}), 0) AS quantity,
            COALESCE(SUM({stock_value_expr}), 0) AS stock_value,
            SUM(CASE WHEN {qty_expr} < 0 THEN 1 ELSE 0 END) AS negative_stock,
            SUM(
                CASE
                    WHEN {qty_expr} > 0
                     AND {expiry_expr} IS NOT NULL
                     AND {expiry_expr} < %(today)s
                    THEN 1 ELSE 0
                END
            ) AS expired_items
        {from_sql}
        GROUP BY b.`warehouse`
        ORDER BY stock_value DESC
        LIMIT 100
        """.format(
            qty_expr=qty_expr,
            stock_value_expr=stock_value_expr,
            expiry_expr=expiry_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    by_warehouse = _normalise_sql_rows(
        by_warehouse,
        integer_fields=("items", "negative_stock", "expired_items"),
        float_fields=("quantity", "stock_value"),
    )

    by_item_group = frappe.db.sql(
        """
        SELECT
            {item_group_expr} AS item_group,
            COALESCE(SUM({qty_expr}), 0) AS quantity,
            COALESCE(SUM({stock_value_expr}), 0) AS stock_value
        {from_sql}
        GROUP BY {item_group_expr}
        ORDER BY stock_value DESC
        LIMIT 15
        """.format(
            item_group_expr=item_group_expr,
            qty_expr=qty_expr,
            stock_value_expr=stock_value_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    by_item_group = _normalise_sql_rows(
        by_item_group,
        float_fields=("quantity", "stock_value"),
    )

    top_items = frappe.db.sql(
        """
        SELECT
            b.`warehouse` AS warehouse,
            i.`item_code` AS item_code,
            MAX({item_name_expr}) AS item_name,
            MAX({item_group_expr}) AS item_group,
            MAX({sub_category_expr}) AS sub_category,
            MAX({code_expr}) AS code,
            COALESCE(SUM({qty_expr}), 0) AS actual_qty,
            COALESCE(SUM({stock_value_expr}), 0) AS stock_value,
            MAX({standard_rate_expr}) AS standard_rate,
            MAX({expiry_expr}) AS expired_date
        {from_sql}
        GROUP BY b.`warehouse`, i.`item_code`
        ORDER BY stock_value DESC
        LIMIT 20
        """.format(
            item_name_expr=item_name_expr,
            item_group_expr=item_group_expr,
            sub_category_expr=sub_category_expr,
            code_expr=code_expr,
            qty_expr=qty_expr,
            stock_value_expr=stock_value_expr,
            standard_rate_expr=standard_rate_expr,
            expiry_expr=expiry_expr,
            from_sql=from_sql,
        ),
        values,
        as_dict=True,
    )
    top_items = _normalise_sql_rows(
        top_items,
        float_fields=("actual_qty", "stock_value", "standard_rate"),
    )
    for row in top_items:
        qty = flt(row.get("actual_qty"))
        row["avg_cost"] = flt(row.get("stock_value")) / qty if qty else 0

    expired_rows = []
    if "expired_date" in item_fields:
        expired_rows = frappe.db.sql(
            """
            SELECT
                b.`warehouse` AS warehouse,
                i.`item_code` AS item_code,
                MAX({item_name_expr}) AS item_name,
                COALESCE(SUM({qty_expr}), 0) AS actual_qty,
                COALESCE(SUM({stock_value_expr}), 0) AS stock_value,
                MAX({expiry_expr}) AS expired_date
            {from_sql}
              AND {qty_expr} > 0
              AND {expiry_expr} IS NOT NULL
              AND {expiry_expr} < %(today)s
            GROUP BY b.`warehouse`, i.`item_code`
            ORDER BY expired_date ASC, stock_value DESC
            LIMIT 15
            """.format(
                item_name_expr=item_name_expr,
                qty_expr=qty_expr,
                stock_value_expr=stock_value_expr,
                expiry_expr=expiry_expr,
                from_sql=from_sql,
            ),
            values,
            as_dict=True,
        )
        expired_rows = _normalise_sql_rows(
            expired_rows,
            float_fields=("actual_qty", "stock_value"),
        )

    total_qty = flt(summary.get("quantity"))
    total_stock_value = flt(summary.get("stock_value"))
    negative_stock = cint(summary.get("negative_stock"))
    expired_count = cint(summary.get("expired_items"))

    currency = None
    if filters.get("company"):
        currency = frappe.db.get_value("Company", filters.company, "default_currency")
    currency = currency or frappe.defaults.get_global_default("currency") or ""

    has_rows = cint(summary.get("warehouse_lines")) > 0
    status = "GREY" if not has_rows else (
        "AMBER" if negative_stock or expired_count else "GREEN"
    )

    warnings.append(
        _("Inventory reads only enabled, non-group warehouses. It is a current Bin snapshot; Item.expired_date does not replace Batch expiry controls.")
    )

    return {
        "available": True,
        "label": "Inventory",
        "status": status,
        "currency": currency,
        "totals": {
            "items": cint(summary.get("items")),
            "warehouse_lines": cint(summary.get("warehouse_lines")),
            "warehouses": cint(summary.get("warehouses")),
            "quantity": round(total_qty, 2),
            "stock_value": round(total_stock_value, 2),
            "average_cost": round(total_stock_value / total_qty, 2) if total_qty else 0,
            "sales_value": round(flt(summary.get("sales_value")), 2),
            "positive_stock": cint(summary.get("positive_stock")),
            "zero_stock": cint(summary.get("zero_stock")),
            "negative_stock": negative_stock,
            "expired_items": expired_count,
        },
        "by_warehouse": by_warehouse,
        "by_item_group": by_item_group,
        "top_items": top_items,
        "expired": expired_rows,
        "report": INVENTORY_REPORT,
        "report_exists": _report_exists(INVENTORY_REPORT),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Sales — uses base-currency fields where available for safe aggregation
# ---------------------------------------------------------------------------


def _get_sales(filters):
    doctype = "Sales Invoice"
    fields = _doctype_fields(doctype)
    if not fields:
        return _unavailable("Sales", _("DocType Sales Invoice was not found."))

    required = {"posting_date", "company", "customer", "status", "docstatus"}
    missing = sorted(required - fields)
    if missing:
        return _unavailable(
            "Sales",
            _("Sales Invoice is missing required fields: {0}").format(", ".join(missing)),
        )

    grand_field = "base_grand_total" if "base_grand_total" in fields else "grand_total"
    outstanding_field = (
        "base_outstanding_amount" if "base_outstanding_amount" in fields else "outstanding_amount"
    )

    conditions = ["si.docstatus = 1"]
    values = {}

    if filters.get("from_date"):
        conditions.append("si.posting_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("si.posting_date <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("company"):
        conditions.append("si.company = %(company)s")
        values["company"] = filters.company
    if filters.get("cost_center") and "cost_center" in fields:
        conditions.append("si.cost_center = %(cost_center)s")
        values["cost_center"] = filters.cost_center
    if filters.get("customer"):
        conditions.append("si.customer = %(customer)s")
        values["customer"] = filters.customer
    if filters.get("sales_status"):
        conditions.append("si.status = %(sales_status)s")
        values["sales_status"] = filters.sales_status

    where_clause = " AND ".join(conditions)
    customer_group_expr = "si.customer_group" if "customer_group" in fields else "NULL"
    territory_expr = "si.territory" if "territory" in fields else "NULL"
    currency_expr = "si.currency" if "currency" in fields else "NULL"

    customer_fields = _doctype_fields("Customer")
    customer_join = ""
    customer_name_candidates = []
    if customer_fields and "customer_name" in customer_fields:
        customer_join = "LEFT JOIN `tabCustomer` customer_master ON customer_master.name = si.customer"
        customer_name_candidates.append("NULLIF(TRIM(customer_master.customer_name), '')")
    if "customer_name" in fields:
        customer_name_candidates.append("NULLIF(TRIM(si.customer_name), '')")
    customer_name_candidates.append("NULLIF(TRIM(si.customer), '')")
    customer_name_expr = "COALESCE({0}, 'Not Set')".format(", ".join(customer_name_candidates))

    invoices = frappe.db.sql(
        """
        SELECT
            si.name,
            si.posting_date,
            si.company,
            si.customer,
            {customer_name_expr} AS customer_name,
            {customer_group_expr} AS customer_group,
            {territory_expr} AS territory,
            si.status,
            {currency_expr} AS currency,
            si.`{grand_field}` AS grand_total,
            si.`{outstanding_field}` AS outstanding_amount
        FROM `tabSales Invoice` si
        {customer_join}
        WHERE {where_clause}
        ORDER BY si.posting_date ASC, si.name ASC
        """.format(
            customer_name_expr=customer_name_expr,
            customer_group_expr=customer_group_expr,
            territory_expr=territory_expr,
            currency_expr=currency_expr,
            grand_field=grand_field,
            outstanding_field=outstanding_field,
            customer_join=customer_join,
            where_clause=where_clause,
        ),
        values,
        as_dict=True,
    )
    invoices = _normalise_sql_rows(
        invoices,
        float_fields=("grand_total", "outstanding_amount"),
    )

    item_groups = []
    item_fields = _doctype_fields("Sales Invoice Item")
    if item_fields and "item_group" in item_fields:
        item_amount_field = "base_net_amount" if "base_net_amount" in item_fields else "net_amount"
        item_rows = frappe.db.sql(
            """
            SELECT
                COALESCE(NULLIF(TRIM(sii.item_group), ''), 'Other') AS item_group,
                SUM(sii.`{amount_field}`) AS amount,
                SUM(sii.qty) AS quantity,
                COUNT(DISTINCT si.name) AS invoice_count
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            WHERE {where_clause}
            GROUP BY COALESCE(NULLIF(TRIM(sii.item_group), ''), 'Other')
            ORDER BY amount DESC
            LIMIT 10
            """.format(amount_field=item_amount_field, where_clause=where_clause),
            values,
            as_dict=True,
        )
        item_groups = _normalise_sql_rows(
            item_rows,
            integer_fields=("invoice_count",),
            float_fields=("amount", "quantity"),
        )

    payments_received = 0.0
    payment_count = 0
    payment_fields = _doctype_fields("Payment Entry")
    if payment_fields and {"posting_date", "payment_type", "company", "docstatus"}.issubset(payment_fields):
        payment_amount_field = (
            "base_received_amount"
            if "base_received_amount" in payment_fields
            else ("received_amount" if "received_amount" in payment_fields else "paid_amount")
        )
        payment_conditions = ["pe.docstatus = 1", "pe.payment_type = 'Receive'"]
        payment_values = {}
        if filters.get("from_date"):
            payment_conditions.append("pe.posting_date >= %(from_date)s")
            payment_values["from_date"] = filters.from_date
        if filters.get("to_date"):
            payment_conditions.append("pe.posting_date <= %(to_date)s")
            payment_values["to_date"] = filters.to_date
        if filters.get("company"):
            payment_conditions.append("pe.company = %(company)s")
            payment_values["company"] = filters.company
        if filters.get("cost_center") and "cost_center" in payment_fields:
            payment_conditions.append("pe.cost_center = %(cost_center)s")
            payment_values["cost_center"] = filters.cost_center

        payment_row = frappe.db.sql(
            """
            SELECT
                COUNT(*) AS payment_count,
                COALESCE(SUM(pe.`{amount_field}`), 0) AS payments_received
            FROM `tabPayment Entry` pe
            WHERE {where_clause}
            """.format(
                amount_field=payment_amount_field,
                where_clause=" AND ".join(payment_conditions),
            ),
            payment_values,
            as_dict=True,
        )
        if payment_row:
            payment_count = cint(payment_row[0].get("payment_count"))
            payments_received = flt(payment_row[0].get("payments_received"))

    currency = None
    if filters.get("company"):
        currency = frappe.db.get_value("Company", filters.company, "default_currency")
    if not currency:
        currency = next((row.get("currency") for row in invoices if row.get("currency")), None)
    currency = currency or frappe.defaults.get_global_default("currency") or ""

    total_revenue = sum(flt(row.get("grand_total")) for row in invoices)
    total_outstanding = sum(flt(row.get("outstanding_amount")) for row in invoices)
    total_collected = total_revenue - total_outstanding
    overdue = sum(1 for row in invoices if row.get("status") == "Overdue")
    paid = sum(1 for row in invoices if row.get("status") == "Paid")

    daily = defaultdict(lambda: {"revenue": 0.0, "outstanding": 0.0})
    customer_map = defaultdict(lambda: {"revenue": 0.0, "outstanding": 0.0, "invoices": 0})
    status_counter = Counter()

    for row in invoices:
        period = str(row.get("posting_date"))
        daily[period]["revenue"] += flt(row.get("grand_total"))
        daily[period]["outstanding"] += flt(row.get("outstanding_amount"))

        customer = row.get("customer") or _("Not Set")
        customer_map[customer]["customer_name"] = row.get("customer_name") or customer
        customer_map[customer]["revenue"] += flt(row.get("grand_total"))
        customer_map[customer]["outstanding"] += flt(row.get("outstanding_amount"))
        customer_map[customer]["invoices"] += 1

        status_counter[row.get("status") or _("Not Set")] += 1

    trend = [
        {
            "period": period,
            "revenue": round(values_["revenue"], 2),
            "outstanding": round(values_["outstanding"], 2),
        }
        for period, values_ in sorted(daily.items())
    ]

    top_customers = [
        {
            "customer": customer,
            "customer_name": values_.get("customer_name") or customer,
            "revenue": round(values_["revenue"], 2),
            "collected": round(values_["revenue"] - values_["outstanding"], 2),
            "outstanding": round(values_["outstanding"], 2),
            "invoices": values_["invoices"],
        }
        for customer, values_ in sorted(
            customer_map.items(), key=lambda item: item[1]["revenue"], reverse=True
        )[:10]
    ]

    status_rows = [
        {"status": label, "count": count}
        for label, count in status_counter.most_common()
    ]

    status = "RED" if overdue else ("AMBER" if total_outstanding else "GREEN")

    return {
        "available": True,
        "label": "Sales",
        "status": status,
        "currency": currency,
        "totals": {
            "invoices": len(invoices),
            "revenue": total_revenue,
            "collected": total_collected,
            "outstanding": total_outstanding,
            "payments_received": payments_received,
            "payment_count": payment_count,
            "paid_invoices": paid,
            "overdue_invoices": overdue,
            "average_invoice": (total_revenue / len(invoices)) if invoices else 0,
        },
        "trend": trend,
        "top_customers": top_customers,
        "item_groups": item_groups,
        "status_rows": status_rows,
        "report": SALES_REPORT,
        "report_exists": _report_exists(SALES_REPORT),
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Finance — inferred backend for the uploaded Financial Dashboard frontend
# ---------------------------------------------------------------------------


def _get_finance(filters, fallback_currency=None):
    if not filters.get("company"):
        return _unavailable(
            "Finance",
            _("Select a Company to calculate the GL-based finance section."),
        )

    gle_fields = _doctype_fields("GL Entry")
    account_fields = _doctype_fields("Account")
    if not gle_fields or not account_fields:
        return _unavailable("Finance", _("GL Entry or Account was not found."))

    required_gle = {"posting_date", "company", "account", "debit", "credit"}
    required_account = {"root_type", "account_type"}
    missing = sorted((required_gle - gle_fields) | (required_account - account_fields))
    if missing:
        return _unavailable(
            "Finance",
            _("Finance source fields are missing: {0}").format(", ".join(missing)),
        )

    base_conditions = ["gle.company = %(company)s"]
    values = {"company": filters.company}

    if "is_cancelled" in gle_fields:
        base_conditions.append("COALESCE(gle.is_cancelled, 0) = 0")
    elif "docstatus" in gle_fields:
        base_conditions.append("gle.docstatus = 1")

    if filters.get("cost_center") and "cost_center" in gle_fields:
        base_conditions.append("gle.cost_center = %(cost_center)s")
        values["cost_center"] = filters.cost_center
    if filters.get("finance_book") and "finance_book" in gle_fields:
        base_conditions.append("gle.finance_book = %(finance_book)s")
        values["finance_book"] = filters.finance_book

    period_conditions = list(base_conditions)
    if filters.get("from_date"):
        period_conditions.append("gle.posting_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        period_conditions.append("gle.posting_date <= %(to_date)s")
        values["to_date"] = filters.to_date

    monthly = frappe.db.sql(
        """
        SELECT
            DATE_FORMAT(gle.posting_date, '%%Y-%%m') AS period,
            SUM(CASE WHEN acc.root_type = 'Income' THEN gle.credit - gle.debit ELSE 0 END) AS income,
            SUM(CASE WHEN acc.root_type = 'Expense' THEN gle.debit - gle.credit ELSE 0 END) AS expense
        FROM `tabGL Entry` gle
        INNER JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE {where_clause}
        GROUP BY DATE_FORMAT(gle.posting_date, '%%Y-%%m')
        ORDER BY period ASC
        """.format(where_clause=" AND ".join(period_conditions)),
        values,
        as_dict=True,
    )
    monthly = _normalise_sql_rows(monthly, float_fields=("income", "expense"))
    for row in monthly:
        row["profit"] = flt(row.income) - flt(row.expense)

    income = sum(flt(row.income) for row in monthly)
    expense = sum(flt(row.expense) for row in monthly)
    net_profit = income - expense

    balance_conditions = list(base_conditions)
    if filters.get("to_date"):
        balance_conditions.append("gle.posting_date <= %(to_date)s")

    balance_row = frappe.db.sql(
        """
        SELECT
            SUM(CASE WHEN acc.account_type = 'Receivable' THEN gle.debit - gle.credit ELSE 0 END) AS receivable,
            SUM(CASE WHEN acc.account_type = 'Payable' THEN gle.credit - gle.debit ELSE 0 END) AS payable
        FROM `tabGL Entry` gle
        INNER JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE {where_clause}
        """.format(where_clause=" AND ".join(balance_conditions)),
        values,
        as_dict=True,
    )
    receivable = flt(balance_row[0].get("receivable")) if balance_row else 0
    payable = flt(balance_row[0].get("payable")) if balance_row else 0

    top_receivables = []
    top_payables = []
    if {"party", "party_type"}.issubset(gle_fields):
        customer_fields = _doctype_fields("Customer")
        customer_join = ""
        if customer_fields and "customer_name" in customer_fields:
            customer_join = "LEFT JOIN `tabCustomer` customer_master ON customer_master.name = gle.party"
            customer_name_expr = (
                "COALESCE(NULLIF(TRIM(customer_master.customer_name), ''), "
                "NULLIF(TRIM(gle.party), ''), 'Unallocated')"
            )
        else:
            customer_name_expr = "COALESCE(NULLIF(TRIM(gle.party), ''), 'Unallocated')"

        top_receivables = frappe.db.sql(
            """
            SELECT
                COALESCE(NULLIF(TRIM(gle.party), ''), 'Unallocated') AS party,
                {customer_name_expr} AS customer_name,
                SUM(gle.debit - gle.credit) AS balance
            FROM `tabGL Entry` gle
            INNER JOIN `tabAccount` acc ON acc.name = gle.account
            {customer_join}
            WHERE {where_clause}
              AND acc.account_type = 'Receivable'
              AND gle.party_type = 'Customer'
            GROUP BY gle.party, {customer_name_expr}
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT 10
            """.format(
                customer_name_expr=customer_name_expr,
                customer_join=customer_join,
                where_clause=" AND ".join(balance_conditions),
            ),
            values,
            as_dict=True,
        )
        top_payables = frappe.db.sql(
            """
            SELECT
                COALESCE(NULLIF(TRIM(gle.party), ''), 'Unallocated') AS party,
                SUM(gle.credit - gle.debit) AS balance
            FROM `tabGL Entry` gle
            INNER JOIN `tabAccount` acc ON acc.name = gle.account
            WHERE {where_clause}
              AND acc.account_type = 'Payable'
              AND gle.party_type = 'Supplier'
            GROUP BY COALESCE(NULLIF(TRIM(gle.party), ''), 'Unallocated')
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT 10
            """.format(where_clause=" AND ".join(balance_conditions)),
            values,
            as_dict=True,
        )
        top_receivables = _normalise_sql_rows(top_receivables, float_fields=("balance",))
        top_payables = _normalise_sql_rows(top_payables, float_fields=("balance",))

    currency = (
        frappe.db.get_value("Company", filters.company, "default_currency")
        or fallback_currency
        or frappe.defaults.get_global_default("currency")
        or ""
    )

    if not monthly and not receivable and not payable:
        status = "GREY"
    else:
        status = "RED" if net_profit < 0 else "GREEN"

    return {
        "available": True,
        "label": "Finance",
        "status": status,
        "currency": currency,
        "totals": {
            "income": income,
            "expense": expense,
            "net_profit": net_profit,
            "receivable": receivable,
            "payable": payable,
        },
        "monthly": monthly,
        "top_receivables": top_receivables,
        "top_payables": top_payables,
        "report": FINANCIAL_REPORT,
        "report_exists": _report_exists(FINANCIAL_REPORT),
        "warnings": [
            _("P&L covers the selected period; receivables and payables are balances as of To Date.")
        ],
    }


# ---------------------------------------------------------------------------
# Alerts, status, cards and charts
# ---------------------------------------------------------------------------


def _build_alerts(sources):
    alerts = []

    def add(severity, domain, title, detail, report=None, tab=None):
        alerts.append(
            {
                "severity": severity,
                "domain": domain,
                "title": title,
                "detail": detail,
                "report": report,
                "tab": tab,
            }
        )

    opd = sources["opd"]
    if opd.get("available") and opd["totals"]["open_cases"]:
        add(
            "RED",
            "OPD",
            _("Pending OPD patients"),
            _("{0} queue records remain Open in the selected period.").format(
                _number(opd["totals"]["open_cases"])
            ),
            OPD_REPORT,
            "opd",
        )

    queue = sources["queue"]
    if queue.get("available"):
        queue_totals = queue["totals"]
        if queue_totals.get("critical_waits"):
            add(
                "RED",
                "Queue Time",
                _("Critical queue-to-encounter delays"),
                _("{0} patient journey(s) exceeded 30 minutes or have no encounter recorded for a past queue date.").format(
                    _number(queue_totals.get("critical_waits"))
                ),
                QUEUE_REPORT if queue.get("report_exists") else None,
                "queue",
            )
        elif queue_totals.get("over_target"):
            add(
                "AMBER",
                "Queue Time",
                _("Queue-to-encounter target exceeded"),
                _("{0} patient journey(s) exceeded the 20-minute target.").format(
                    _number(queue_totals.get("over_target"))
                ),
                QUEUE_REPORT if queue.get("report_exists") else None,
                "queue",
            )

    ipd = sources["ipd"]
    if ipd.get("available") and ipd["totals"]["cancelled"]:
        add(
            "RED",
            "IPD",
            _("Cancelled inpatient records"),
            _("{0} IPD records are Cancelled in the selected period.").format(
                _number(ipd["totals"]["cancelled"])
            ),
            IPD_REPORT,
            "ipd",
        )

    ot = sources["ot"]
    if ot.get("available") and ot["totals"]["pending_preop"]:
        add(
            "AMBER",
            "OT",
            _("OT pre-operative requirements pending"),
            _("{0} OT case(s) have one or more incomplete pre-operative requirements.").format(
                _number(ot["totals"]["pending_preop"])
            ),
            OT_REPORT if ot.get("report_exists") else None,
            "ot",
        )

    lab = sources["lab"]
    if lab.get("available"):
        abnormal_rate = lab["totals"].get("abnormal_rate")
        if abnormal_rate is not None and abnormal_rate > 40:
            add(
                "RED",
                "Laboratory",
                _("High abnormal-result rate"),
                _("{0} of evaluated Blood analytes are abnormal.").format(
                    _percent(abnormal_rate)
                ),
                LAB_REPORT,
                "lab",
            )
        elif abnormal_rate is not None and abnormal_rate > 0:
            add(
                "AMBER",
                "Laboratory",
                _("Abnormal laboratory results present"),
                _("{0} of evaluated Blood analytes are abnormal.").format(
                    _percent(abnormal_rate)
                ),
                LAB_REPORT,
                "lab",
            )

        coverage = lab["totals"].get("evaluation_coverage")
        if coverage is not None and coverage < 80:
            add(
                "AMBER",
                "Laboratory",
                _("Low result-evaluation coverage"),
                _("Only {0} of analyte rows could be evaluated against a reference range.").format(
                    _percent(coverage)
                ),
                LAB_REPORT,
                "lab",
            )

    pharmacy = sources["pharmacy"]
    if pharmacy.get("available"):
        totals = pharmacy.get("totals") or {}
        if totals.get("stockouts"):
            add(
                "RED",
                "Pharmacy",
                _("Pharmacy stockouts"),
                _("{0} pharmacy item(s) are unavailable because quantity is zero/negative or the item is expired.").format(
                    _number(totals.get("stockouts"))
                ),
                INVENTORY_REPORT if pharmacy.get("report_exists") else None,
                "pharmacy",
            )
        if totals.get("expired_stock"):
            add(
                "RED",
                "Pharmacy",
                _("Expired pharmacy stock"),
                _("{0} pharmacy item(s) have positive stock and an expired Item date.").format(
                    _number(totals.get("expired_stock"))
                ),
                INVENTORY_REPORT if pharmacy.get("report_exists") else None,
                "pharmacy",
            )
        if totals.get("low_stock"):
            add(
                "AMBER",
                "Pharmacy",
                _("Low pharmacy stock"),
                _("{0} pharmacy item(s) are at or below the resolved reorder level.").format(
                    _number(totals.get("low_stock"))
                ),
                INVENTORY_REPORT if pharmacy.get("report_exists") else None,
                "pharmacy",
            )
        if totals.get("near_expiry"):
            add(
                "AMBER",
                "Pharmacy",
                _("Pharmacy items near expiry"),
                _("{0} pharmacy item(s) expire in fewer than 90 days.").format(
                    _number(totals.get("near_expiry"))
                ),
                INVENTORY_REPORT if pharmacy.get("report_exists") else None,
                "pharmacy",
            )
        if totals.get("pending_pos"):
            add(
                "AMBER",
                "Pharmacy",
                _("Pending pharmacy purchase orders"),
                _("{0} submitted Purchase Order(s) are not Completed.").format(
                    _number(totals.get("pending_pos"))
                ),
                None,
                "pharmacy",
            )

    warehouse = sources["warehouse"]
    if warehouse.get("available"):
        totals = warehouse.get("totals") or {}
        if totals.get("critical_unavailable"):
            add(
                "RED",
                "Warehouse",
                _("Critical drugs unavailable"),
                _("{0} critical item(s) are at zero or negative stock.").format(
                    _number(totals.get("critical_unavailable"))
                ),
                INVENTORY_REPORT if warehouse.get("report_exists") else None,
                "warehouse",
            )
        if totals.get("stockouts"):
            add(
                "RED",
                "Warehouse",
                _("Warehouse stockouts"),
                _("{0} mapped item(s) are unavailable because quantity is zero/negative or the item is expired.").format(
                    _number(totals.get("stockouts"))
                ),
                INVENTORY_REPORT if warehouse.get("report_exists") else None,
                "warehouse",
            )
        if totals.get("expired_stock"):
            add(
                "RED",
                "Warehouse",
                _("Expired warehouse stock"),
                _("{0} item(s) have positive stock and an expired Item date.").format(
                    _number(totals.get("expired_stock"))
                ),
                INVENTORY_REPORT if warehouse.get("report_exists") else None,
                "warehouse",
            )
        if totals.get("low_stock"):
            add(
                "AMBER",
                "Warehouse",
                _("Low warehouse stock"),
                _("{0} item(s) are at or below the resolved reorder level.").format(
                    _number(totals.get("low_stock"))
                ),
                INVENTORY_REPORT if warehouse.get("report_exists") else None,
                "warehouse",
            )
        if totals.get("near_expiry"):
            add(
                "AMBER",
                "Warehouse",
                _("Warehouse items near expiry"),
                _("{0} item(s) expire in fewer than 90 days.").format(
                    _number(totals.get("near_expiry"))
                ),
                INVENTORY_REPORT if warehouse.get("report_exists") else None,
                "warehouse",
            )
        if totals.get("pending_pos"):
            add(
                "AMBER",
                "Warehouse",
                _("Pending warehouse purchase orders"),
                _("{0} submitted Purchase Order(s) are not Completed.").format(
                    _number(totals.get("pending_pos"))
                ),
                None,
                "warehouse",
            )

    optical = sources["optical"]
    if optical.get("available"):
        totals = optical.get("totals") or {}
        if totals.get("frame_stockouts"):
            add(
                "RED",
                "Optical",
                _("Optical frame stockouts"),
                _("{0} frame item(s) are unavailable because quantity is zero/negative or the item is expired.").format(
                    _number(totals.get("frame_stockouts"))
                ),
                None,
                "optical",
            )
        if totals.get("pending_glasses_orders"):
            add(
                "AMBER",
                "Optical",
                _("Pending glasses orders"),
                _("{0} submitted Sales Order(s) for optical items remain pending.").format(
                    _number(totals.get("pending_glasses_orders"))
                ),
                None,
                "optical",
            )

    staffing = sources["staffing"]
    if staffing.get("available"):
        totals = staffing.get("totals") or {}
        if totals.get("absent_staff"):
            add(
                "RED",
                "HR Attendance",
                _("Absent staff"),
                _("{0} staff member(s) have no accepted Employee Checkin: {1} doctor(s), {2} nurse(s).").format(
                    _number(totals.get("absent_staff")),
                    _number(totals.get("absent_doctors")),
                    _number(totals.get("absent_nurses")),
                ),
                ATTENDANCE_REPORT if staffing.get("report_exists") else None,
                "hr",
            )
        elif totals.get("not_checked_in"):
            add(
                "AMBER",
                "HR Attendance",
                _("Staff not checked in"),
                _("{0} active staff member(s) are not checked in yet.").format(
                    _number(totals.get("not_checked_in"))
                ),
                ATTENDANCE_REPORT if staffing.get("report_exists") else None,
                "hr",
            )

    inventory = sources["inventory"]
    if inventory.get("available"):
        inventory_totals = inventory["totals"]
        if inventory_totals.get("negative_stock"):
            add(
                "AMBER",
                "Inventory",
                _("Negative warehouse stock"),
                _("{0} warehouse/item line(s) have a negative quantity.").format(
                    _number(inventory_totals.get("negative_stock"))
                ),
                INVENTORY_REPORT if inventory.get("report_exists") else None,
                "inventory",
            )
        if inventory_totals.get("expired_items"):
            add(
                "AMBER",
                "Inventory",
                _("Expired items identified"),
                _("{0} warehouse/item line(s) have positive quantity and an expired Item date.").format(
                    _number(inventory_totals.get("expired_items"))
                ),
                INVENTORY_REPORT if inventory.get("report_exists") else None,
                "inventory",
            )

    sales = sources["sales"]
    if sales.get("available"):
        if sales["totals"]["overdue_invoices"]:
            add(
                "RED",
                "Sales",
                _("Overdue invoices"),
                _("{0} submitted invoices are Overdue.").format(
                    _number(sales["totals"]["overdue_invoices"])
                ),
                SALES_REPORT,
                "sales",
            )
        elif sales["totals"]["outstanding"]:
            add(
                "AMBER",
                "Sales",
                _("Outstanding receivables"),
                _("Outstanding invoice amount: {0}.").format(
                    _money(sales["totals"]["outstanding"], sales.get("currency"))
                ),
                SALES_REPORT,
                "sales",
            )

    finance = sources["finance"]
    if finance.get("available") and finance["totals"]["net_profit"] < 0:
        add(
            "RED",
            "Finance",
            _("Negative net profit"),
            _("Net profit for the selected period is {0}.").format(
                _money(finance["totals"]["net_profit"], finance.get("currency"))
            ),
            FINANCIAL_REPORT if finance.get("report_exists") else None,
            "finance",
        )

    return sorted(alerts, key=lambda item: _status_rank(item["severity"]), reverse=True)


def _build_notices(sources):
    notices = []
    for key, source in sources.items():
        if not source.get("available"):
            notices.append(
                {
                    "source": source.get("label") or key.title(),
                    "message": source.get("reason") or _("Source unavailable."),
                }
            )
        for warning in source.get("warnings") or []:
            notices.append(
                {
                    "source": source.get("label") or key.title(),
                    "message": warning,
                }
            )
    return notices


def _overall_status(alerts, sources):
    red = sum(1 for alert in alerts if alert["severity"] == "RED")
    amber = sum(1 for alert in alerts if alert["severity"] == "AMBER")
    available = sum(1 for source in sources.values() if source.get("available"))

    if red:
        return {
            "status": "RED",
            "label": _("Immediate attention required"),
            "reason": _("{0} red exception(s) require action.").format(red),
        }
    if amber:
        return {
            "status": "AMBER",
            "label": _("Review exceptions"),
            "reason": _("{0} amber exception(s) require review.").format(amber),
        }
    if available:
        return {
            "status": "GREEN",
            "label": _("No configured exceptions"),
            "reason": _("The available data sources have no active configured alert."),
        }
    return {
        "status": "GREY",
        "label": _("No source data"),
        "reason": _("None of the configured dashboard sources is currently available."),
    }


def _build_kpi_cards(sources, alerts):
    cards = []

    def add(code, label, display, subtext, status, tab, report=None):
        cards.append(
            {
                "code": code,
                "label": label,
                "display": display,
                "subtext": subtext,
                "status": status,
                "tab": tab,
                "report": report,
            }
        )

    opd = sources["opd"]
    if opd.get("available"):
        add(
            "opd_total",
            _("OPD Patients"),
            _number(opd["totals"]["total_cases"]),
            _("Selected period"),
            "BLUE",
            "opd",
            OPD_REPORT,
        )
        add(
            "opd_pending",
            _("Pending OPD"),
            _number(opd["totals"]["open_cases"]),
            _("Status = Open"),
            "RED" if opd["totals"]["open_cases"] else "GREEN",
            "opd",
            OPD_REPORT,
        )

    queue = sources["queue"]
    if queue.get("available"):
        totals = queue["totals"]
        add(
            "queue_waiting",
            _("Waiting for Encounter"),
            _number(totals.get("waiting")),
            _("Queue date {0}").format(queue.get("queue_date")),
            queue.get("status", "GREY") if totals.get("waiting") else "GREEN",
            "queue",
            QUEUE_REPORT if queue.get("report_exists") else None,
        )
        add(
            "queue_average",
            _("Average Queue Wait"),
            _minutes_display(totals.get("average_wait")),
            _("Target ≤ 20 min"),
            _wait_status(totals.get("average_wait")),
            "queue",
            QUEUE_REPORT if queue.get("report_exists") else None,
        )
        add(
            "queue_longest",
            _("Longest Queue Wait"),
            _minutes_display(totals.get("longest_wait")),
            _("Critical > 30 min"),
            _wait_status(totals.get("longest_wait")),
            "queue",
            QUEUE_REPORT if queue.get("report_exists") else None,
        )

    ipd = sources["ipd"]
    if ipd.get("available"):
        add(
            "ipd_total",
            _("IPD Records"),
            _number(ipd["totals"]["total_patients"]),
            _("Created in selected period"),
            "BLUE",
            "ipd",
            IPD_REPORT,
        )
        add(
            "ipd_admitted",
            _("Admitted"),
            _number(ipd["totals"]["admitted"]),
            _("IPD status"),
            "GREEN",
            "ipd",
            IPD_REPORT,
        )

    ot = sources["ot"]
    if ot.get("available"):
        add(
            "ot_cases",
            _("OT Cases"),
            _number(ot["totals"]["cases"]),
            _("Selected period"),
            "BLUE",
            "ot",
            OT_REPORT if ot.get("report_exists") else None,
        )
        add(
            "ot_pending",
            _("OT Pre-op Pending"),
            _number(ot["totals"]["pending_preop"]),
            _("Incomplete requirements"),
            "AMBER" if ot["totals"]["pending_preop"] else "GREEN",
            "ot",
            OT_REPORT if ot.get("report_exists") else None,
        )

    lab = sources["lab"]
    if lab.get("available"):
        add(
            "lab_documents",
            _("Lab Results"),
            _number(lab["totals"]["documents"]),
            _("Distinct Blood documents"),
            "BLUE",
            "lab",
            LAB_REPORT,
        )
        add(
            "lab_analytes",
            _("Tests Performed"),
            _number(lab["totals"]["analytes"]),
            _("Analyte rows"),
            "BLUE",
            "lab",
            LAB_REPORT,
        )
        add(
            "lab_abnormal",
            _("Abnormal Rate"),
            _percent(lab["totals"]["abnormal_rate"]),
            _("Evaluated analytes"),
            lab.get("status", "GREY"),
            "lab",
            LAB_REPORT,
        )

    pharmacy = sources["pharmacy"]
    if pharmacy.get("available"):
        totals = pharmacy.get("totals") or {}
        add(
            "pharmacy_stockouts",
            _("Pharmacy Stockouts"),
            _optional_number(totals.get("stockouts")),
            _("Enabled pharmacy warehouses"),
            "RED" if totals.get("stockouts") else "GREEN",
            "pharmacy",
            INVENTORY_REPORT if pharmacy.get("report_exists") else None,
        )
        add(
            "pharmacy_near_expiry",
            _("Pharmacy Near Expiry"),
            _optional_number(totals.get("near_expiry")),
            _("Fewer than 90 days"),
            "GREY" if totals.get("near_expiry") is None else ("AMBER" if totals.get("near_expiry") else "GREEN"),
            "pharmacy",
            INVENTORY_REPORT if pharmacy.get("report_exists") else None,
        )

    warehouse = sources["warehouse"]
    if warehouse.get("available"):
        totals = warehouse.get("totals") or {}
        add(
            "warehouse_critical",
            _("Critical Drugs Unavailable"),
            _optional_number(totals.get("critical_unavailable")),
            _("Enabled warehouses"),
            "GREY" if totals.get("critical_unavailable") is None else ("RED" if totals.get("critical_unavailable") else "GREEN"),
            "warehouse",
            INVENTORY_REPORT if warehouse.get("report_exists") else None,
        )

    optical = sources["optical"]
    if optical.get("available"):
        totals = optical.get("totals") or {}
        add(
            "optical_pending",
            _("Pending Glasses Orders"),
            _optional_number(totals.get("pending_glasses_orders")),
            _("Submitted Sales Orders"),
            "GREY" if totals.get("pending_glasses_orders") is None else ("AMBER" if totals.get("pending_glasses_orders") else "GREEN"),
            "optical",
            None,
        )

    staffing = sources["staffing"]
    if staffing.get("available"):
        totals = staffing.get("totals") or {}
        add(
            "staff_absent",
            _("Absent Staff"),
            _number(totals.get("absent_staff")),
            _("Employee Checkin · {0}").format(staffing.get("attendance_date")),
            "RED" if totals.get("absent_staff") else "GREEN",
            "hr",
            ATTENDANCE_REPORT if staffing.get("report_exists") else None,
        )

    hr = sources["hr"]
    if hr.get("available"):
        totals = hr["totals"]
        add(
            "hr_employees",
            _("Active Employees"),
            _("{0} / {1}").format(
                _number(totals.get("active")),
                _number(totals.get("employees")),
            ),
            _("Active / total employees"),
            "BLUE",
            "hr",
            HR_REPORT if hr.get("report_exists") else None,
        )

    inventory = sources["inventory"]
    if inventory.get("available"):
        totals = inventory["totals"]
        add(
            "inventory_value",
            _("Stock Value"),
            _money(totals.get("stock_value"), inventory.get("currency")),
            _("Grouped by warehouse"),
            "GREEN" if not totals.get("negative_stock") else "AMBER",
            "inventory",
            INVENTORY_REPORT if inventory.get("report_exists") else None,
        )
        add(
            "inventory_negative",
            _("Negative Stock Lines"),
            _number(totals.get("negative_stock")),
            _("Warehouse/item combinations"),
            "AMBER" if totals.get("negative_stock") else "GREEN",
            "inventory",
            INVENTORY_REPORT if inventory.get("report_exists") else None,
        )

    sales = sources["sales"]
    if sales.get("available"):
        currency = sales.get("currency")
        add(
            "sales_revenue",
            _("Sales Revenue"),
            _money(sales["totals"]["revenue"], currency),
            _("Submitted invoices"),
            "GREEN",
            "sales",
            SALES_REPORT,
        )
        add(
            "sales_outstanding",
            _("Outstanding"),
            _money(sales["totals"]["outstanding"], currency),
            _("Invoice balance"),
            "AMBER" if sales["totals"]["outstanding"] else "GREEN",
            "sales",
            SALES_REPORT,
        )

    finance = sources["finance"]
    if finance.get("available"):
        currency = finance.get("currency")
        add(
            "finance_profit",
            _("Net Profit"),
            _money(finance["totals"]["net_profit"], currency),
            _("GL income minus expense"),
            "RED" if finance["totals"]["net_profit"] < 0 else "GREEN",
            "finance",
            FINANCIAL_REPORT if finance.get("report_exists") else None,
        )
        add(
            "finance_ar",
            _("Receivables"),
            _money(finance["totals"]["receivable"], currency),
            _("Balance as of To Date"),
            "AMBER" if finance["totals"]["receivable"] else "GREEN",
            "finance",
            FINANCIAL_REPORT if finance.get("report_exists") else None,
        )

    add(
        "alerts",
        _("Open Exceptions"),
        _number(len(alerts)),
        _("Red and amber items"),
        "RED" if any(a["severity"] == "RED" for a in alerts) else ("AMBER" if alerts else "GREEN"),
        "overview",
        None,
    )

    return cards


def _build_charts(sources):
    charts = {}

    opd = sources["opd"]
    if opd.get("available"):
        totals = opd["totals"]
        charts["opd_mix"] = _chart(
            "donut",
            [_("New"), _("Follow Up"), _("Refer"), _("Revisit")],
            [[
                totals["new_patient"],
                totals["follow_up"],
                totals["refer"],
                totals["revisit"],
            ]],
            [_ ("Patients")],
            ["#2563eb", "#f59e0b", "#7c3aed", "#0f766e"],
            260,
        )
        charts["opd_status"] = _chart(
            "donut",
            [_("Pending"), _("Completed")],
            [[totals["open_cases"], totals["closed_cases"]]],
            [_ ("Patients")],
            ["#dc2626", "#16a34a"],
            260,
        )
        doctor_rows = opd.get("by_doctor") or []
        charts["opd_doctor"] = _chart(
            "bar",
            [row.get("practitioner") for row in doctor_rows],
            [
                [row.get("new_patient") for row in doctor_rows],
                [row.get("follow_up") for row in doctor_rows],
                [row.get("refer") for row in doctor_rows],
            ],
            [_("New"), _("Follow Up"), _("Refer")],
            ["#2563eb", "#f59e0b", "#7c3aed"],
            320,
        )
        trend = opd.get("trend") or []
        charts["opd_trend"] = _chart(
            "axis-mixed",
            [str(row.get("period")) for row in trend],
            [
                [row.get("total_cases") for row in trend],
                [row.get("open_cases") for row in trend],
            ],
            [_("Total"), _("Pending")],
            ["#2563eb", "#dc2626"],
            300,
            chart_types=["bar", "line"],
        )

    queue = sources["queue"]
    if queue.get("available"):
        totals = queue["totals"]
        charts["queue_stages"] = _chart(
            "bar",
            [_('Queued'), _('Encountered'), _('Sample Collected'), _('Lab Result')],
            [[
                totals.get("patients"),
                totals.get("encountered"),
                totals.get("samples_collected"),
                totals.get("lab_results"),
            ]],
            [_('Patients')],
            ["#0ea5e9"],
            280,
        )
        wait_buckets = queue.get("wait_buckets") or []
        charts["queue_wait_status"] = _chart(
            "donut",
            [row.get("label") for row in wait_buckets],
            [[row.get("value") for row in wait_buckets]],
            [_('Patients')],
            ["#16a34a", "#f59e0b", "#dc2626", "#94a3b8"],
            280,
        )
        doctor_rows = queue.get("by_doctor") or []
        charts["queue_doctor_wait"] = _chart(
            "bar",
            [row.get("practitioner") for row in doctor_rows],
            [
                [row.get("average_wait") for row in doctor_rows],
                [row.get("longest_wait") for row in doctor_rows],
            ],
            [_('Average Wait'), _('Longest Wait')],
            ["#0ea5e9", "#dc2626"],
            320,
        )
        hourly = queue.get("hourly") or []
        charts["queue_hourly"] = _chart(
            "axis-mixed",
            [row.get("period") for row in hourly],
            [
                [row.get("queued") for row in hourly],
                [row.get("encountered") for row in hourly],
            ],
            [_('Queued'), _('Encountered')],
            ["#0ea5e9", "#16a34a"],
            300,
            chart_types=["bar", "line"],
        )

    ipd = sources["ipd"]
    if ipd.get("available"):
        totals = ipd["totals"]
        charts["ipd_status"] = _chart(
            "donut",
            [
                _("Admission Scheduled"),
                _("Admitted"),
                _("Discharge Scheduled"),
                _("Discharged"),
                _("Cancelled"),
            ],
            [[
                totals["admission_scheduled"],
                totals["admitted"],
                totals["discharge_scheduled"],
                totals["discharged"],
                totals["cancelled"],
            ]],
            [_ ("Patients")],
            ["#f59e0b", "#2563eb", "#7c3aed", "#16a34a", "#dc2626"],
            280,
        )
        doctor_rows = ipd.get("by_doctor") or []
        charts["ipd_doctor"] = _chart(
            "bar",
            [row.get("practitioner") for row in doctor_rows],
            [
                [row.get("admitted") for row in doctor_rows],
                [row.get("discharged") for row in doctor_rows],
                [row.get("cancelled") for row in doctor_rows],
            ],
            [_("Admitted"), _("Discharged"), _("Cancelled")],
            ["#2563eb", "#16a34a", "#dc2626"],
            320,
        )
        trend = ipd.get("trend") or []
        charts["ipd_trend"] = _chart(
            "axis-mixed",
            [str(row.get("period")) for row in trend],
            [
                [row.get("total_patients") for row in trend],
                [row.get("cancelled") for row in trend],
            ],
            [_("IPD Records"), _("Cancelled")],
            ["#0f766e", "#dc2626"],
            300,
            chart_types=["bar", "line"],
        )

    ot = sources["ot"]
    if ot.get("available"):
        totals = ot["totals"]
        trend = ot.get("trend") or []
        charts["ot_trend"] = _chart(
            "axis-mixed",
            [str(row.get("period")) for row in trend],
            [
                [row.get("cases") for row in trend],
                [row.get("amount") for row in trend],
            ],
            [_('OT Cases'), _('Amount')],
            ["#7c3aed", "#0f766e"],
            300,
            chart_types=["bar", "line"],
        )
        charts["ot_readiness"] = _chart(
            "bar",
            [
                _("Laboratory Tests"),
                _("IOL Calculation"),
                _("Consent Signed"),
                _("Admission Done"),
                _("Pre-op Ready"),
            ],
            [[
                totals["lab_done"],
                totals["iol_done"],
                totals["consent_done"],
                totals["admission_done"],
                totals["preop_ready"],
            ]],
            [_('Cases')],
            ["#16a34a"],
            300,
        )
        procedures = ot.get("top_procedures") or []
        charts["ot_procedures"] = _chart(
            "bar",
            [row.get("label") for row in procedures],
            [[row.get("value") for row in procedures]],
            [_('Cases')],
            ["#2563eb"],
            320,
        )
        eye_sides = ot.get("eye_sides") or []
        charts["ot_eye_side"] = _chart(
            "donut",
            [row.get("label") for row in eye_sides],
            [[row.get("value") for row in eye_sides]],
            [_('Cases')],
            ["#2563eb", "#7c3aed", "#16a34a", "#f59e0b", "#94a3b8"],
            280,
        )

    lab = sources["lab"]
    if lab.get("available"):
        totals = lab["totals"]
        charts["lab_mix"] = _chart(
            "donut",
            [_("Normal"), _("Abnormal"), _("Not Evaluated")],
            [[totals["normal"], totals["abnormal"], totals["not_evaluated"]]],
            [_ ("Analytes")],
            ["#16a34a", "#dc2626", "#94a3b8"],
            280,
        )
        top_tests = lab.get("top_tests") or []
        charts["lab_top_tests"] = _chart(
            "bar",
            [row.get("label") for row in top_tests],
            [[row.get("value") for row in top_tests]],
            [_ ("Tests")],
            ["#0891b2"],
            320,
        )
        trend = lab.get("trend") or []
        charts["lab_trend"] = _chart(
            "bar",
            [str(row.get("period")) for row in trend],
            [
                [row.get("normal") for row in trend],
                [row.get("abnormal") for row in trend],
            ],
            [_("Normal"), _("Abnormal")],
            ["#16a34a", "#dc2626"],
            300,
        )
        by_department = lab.get("by_department") or []
        charts["lab_department"] = _chart(
            "bar",
            [row.get("department") for row in by_department],
            [
                [row.get("normal") for row in by_department],
                [row.get("abnormal") for row in by_department],
            ],
            [_("Normal"), _("Abnormal")],
            ["#16a34a", "#dc2626"],
            320,
        )

    pharmacy = sources["pharmacy"]
    if pharmacy.get("available"):
        totals = pharmacy.get("totals") or {}
        charts["pharmacy_exceptions"] = _chart(
            "bar",
            [_ ("Stockouts"), _("Low Stock"), _("Near Expiry"), _("Expired")],
            [[
                totals.get("stockouts"),
                totals.get("low_stock"),
                totals.get("near_expiry"),
                totals.get("expired_stock"),
            ]],
            [_ ("Items")],
            ["#dc2626"],
            290,
        )
        pharmacy_warehouses = pharmacy.get("by_warehouse") or []
        charts["pharmacy_warehouse"] = _chart(
            "bar",
            [row.get("warehouse") for row in pharmacy_warehouses],
            [
                [row.get("stockouts") for row in pharmacy_warehouses],
                [row.get("low_stock") for row in pharmacy_warehouses],
            ],
            [_ ("Stockouts"), _("Low Stock")],
            ["#dc2626", "#f59e0b"],
            320,
        )

    warehouse = sources["warehouse"]
    if warehouse.get("available"):
        totals = warehouse.get("totals") or {}
        charts["warehouse_exceptions"] = _chart(
            "bar",
            [_ ("Stockouts"), _("Low Stock"), _("Critical Unavailable"), _("Near Expiry"), _("Expired")],
            [[
                totals.get("stockouts"),
                totals.get("low_stock"),
                totals.get("critical_unavailable"),
                totals.get("near_expiry"),
                totals.get("expired_stock"),
            ]],
            [_ ("Items")],
            ["#ea580c"],
            300,
        )
        warehouse_rows = warehouse.get("by_warehouse") or []
        charts["warehouse_by_location"] = _chart(
            "bar",
            [row.get("warehouse") for row in warehouse_rows],
            [
                [row.get("stockouts") for row in warehouse_rows],
                [row.get("low_stock") for row in warehouse_rows],
                [row.get("near_expiry") for row in warehouse_rows],
            ],
            [_ ("Stockouts"), _("Low Stock"), _("Near Expiry")],
            ["#dc2626", "#f59e0b", "#7c3aed"],
            340,
        )

    optical = sources["optical"]
    if optical.get("available"):
        totals = optical.get("totals") or {}
        charts["optical_activity"] = _chart(
            "bar",
            [
                _("Eye Patients Seen"),
                _("Prescribed Glasses"),
                _("Glasses Sold"),
                _("Frames Sold"),
                _("Pending Orders"),
                _("Frame Stockouts"),
            ],
            [[
                totals.get("eye_patients_seen"),
                totals.get("glasses_prescribed_patients"),
                totals.get("glasses_sold"),
                totals.get("frames_sold"),
                totals.get("pending_glasses_orders"),
                totals.get("frame_stockouts"),
            ]],
            [_ ("Count / Quantity")],
            ["#2563eb"],
            320,
        )
        optical_trend = optical.get("trend") or []
        charts["optical_trend"] = _chart(
            "line",
            [str(row.get("period")) for row in optical_trend],
            [[row.get("patients") for row in optical_trend]],
            [_ ("Eye Patients Seen")],
            ["#0f766e"],
            290,
        )

    staffing = sources["staffing"]
    if staffing.get("available"):
        department_rows = staffing.get("by_department") or []
        charts["staff_absence_department"] = _chart(
            "bar",
            [row.get("department") for row in department_rows],
            [
                [row.get("present_staff") for row in department_rows],
                [row.get("absent_staff") for row in department_rows],
                [row.get("not_checked_in") for row in department_rows],
            ],
            [_ ("Present"), _("Absent"), _("Not Checked In")],
            ["#16a34a", "#dc2626", "#f59e0b"],
            330,
        )
        totals = staffing.get("totals") or {}
        other_staff = max(
            0,
            cint(totals.get("absent_staff"))
            - cint(totals.get("absent_doctors"))
            - cint(totals.get("absent_nurses")),
        )
        charts["staff_absence_mix"] = _chart(
            "donut",
            [_ ("Doctors"), _("Nurses"), _("Other Staff")],
            [[
                totals.get("absent_doctors"),
                totals.get("absent_nurses"),
                other_staff,
            ]],
            [_ ("Absent")],
            ["#7c3aed", "#2563eb", "#dc2626"],
            280,
        )

    hr = sources["hr"]
    if hr.get("available"):
        departments = hr.get("by_department") or []
        charts["hr_department"] = _chart(
            "axis-mixed",
            [row.get("department") for row in departments],
            [
                [row.get("employees") for row in departments],
                [row.get("salary_cost") for row in departments],
            ],
            [_("Employee Count"), _("Salary Cost")],
            ["#0f766e", "#7c3aed"],
            320,
            chart_types=["bar", "line"],
        )
        statuses = hr.get("status_rows") or []
        charts["hr_status"] = _chart(
            "donut",
            [row.get("label") for row in statuses],
            [[row.get("value") for row in statuses]],
            [_("Employees")],
            ["#16a34a", "#f59e0b", "#dc2626", "#64748b", "#2563eb"],
            280,
        )
        employment_types = hr.get("employment_type_rows") or []
        charts["hr_employment_type"] = _chart(
            "bar",
            [row.get("label") for row in employment_types],
            [[row.get("value") for row in employment_types]],
            [_("Employees")],
            ["#7c3aed"],
            300,
        )
        genders = hr.get("gender_rows") or []
        charts["hr_gender"] = _chart(
            "donut",
            [row.get("label") for row in genders],
            [[row.get("value") for row in genders]],
            [_("Employees")],
            ["#2563eb", "#db2777", "#94a3b8", "#16a34a"],
            280,
        )

    inventory = sources["inventory"]
    if inventory.get("available"):
        warehouses = inventory.get("by_warehouse") or []
        charts["inventory_warehouse_value"] = _chart(
            "bar",
            [row.get("warehouse") for row in warehouses],
            [[row.get("stock_value") for row in warehouses]],
            [_("Stock Value")],
            ["#ea580c"],
            330,
        )
        charts["inventory_warehouse_qty"] = _chart(
            "bar",
            [row.get("warehouse") for row in warehouses],
            [[row.get("quantity") for row in warehouses]],
            [_("Current Quantity")],
            ["#0f766e"],
            330,
        )
        item_groups = inventory.get("by_item_group") or []
        charts["inventory_item_group"] = _chart(
            "bar",
            [row.get("item_group") for row in item_groups],
            [[row.get("stock_value") for row in item_groups]],
            [_("Stock Value")],
            ["#7c3aed"],
            330,
        )
        totals = inventory["totals"]
        charts["inventory_stock_health"] = _chart(
            "donut",
            [_("Positive"), _("Zero"), _("Negative")],
            [[
                totals.get("positive_stock"),
                totals.get("zero_stock"),
                totals.get("negative_stock"),
            ]],
            [_("Warehouse/Item Lines")],
            ["#16a34a", "#94a3b8", "#dc2626"],
            280,
        )

    sales = sources["sales"]
    if sales.get("available"):
        trend = sales.get("trend") or []
        charts["sales_trend"] = _chart(
            "axis-mixed",
            [str(row.get("period")) for row in trend],
            [
                [row.get("revenue") for row in trend],
                [row.get("outstanding") for row in trend],
            ],
            [_("Revenue"), _("Outstanding")],
            ["#16a34a", "#dc2626"],
            300,
            chart_types=["bar", "line"],
        )
        statuses = sales.get("status_rows") or []
        charts["sales_status"] = _chart(
            "donut",
            [row.get("status") for row in statuses],
            [[row.get("count") for row in statuses]],
            [_ ("Invoices")],
            ["#16a34a", "#2563eb", "#f59e0b", "#dc2626", "#7c3aed", "#94a3b8"],
            280,
        )
        customers = sales.get("top_customers") or []
        charts["sales_customers"] = _chart(
            "bar",
            [row.get("customer_name") or row.get("customer") for row in customers],
            [[row.get("revenue") for row in customers]],
            [_ ("Revenue")],
            ["#2563eb"],
            320,
        )
        item_groups = sales.get("item_groups") or []
        charts["sales_items"] = _chart(
            "bar",
            [row.get("item_group") for row in item_groups],
            [[row.get("amount") for row in item_groups]],
            [_ ("Net Amount")],
            ["#7c3aed"],
            320,
        )

    finance = sources["finance"]
    if finance.get("available"):
        monthly = finance.get("monthly") or []
        charts["finance_pnl"] = _chart(
            "axis-mixed",
            [str(row.get("period")) for row in monthly],
            [
                [row.get("income") for row in monthly],
                [row.get("expense") for row in monthly],
                [row.get("profit") for row in monthly],
            ],
            [_("Income"), _("Expense"), _("Net Profit")],
            ["#16a34a", "#dc2626", "#2563eb"],
            320,
            chart_types=["bar", "bar", "line"],
        )
        totals = finance["totals"]
        charts["finance_balance"] = _chart(
            "bar",
            [_("Receivables"), _("Payables")],
            [[totals["receivable"], totals["payable"]]],
            [_ ("Balance")],
            ["#f59e0b"],
            280,
        )

    return charts


def _chart(chart_type, labels, values, dataset_names, colors, height, chart_types=None):
    datasets = []
    for index, series in enumerate(values):
        dataset = {
            "name": dataset_names[index] if index < len(dataset_names) else _("Series"),
            "values": [flt(value) for value in series],
        }
        if chart_types and index < len(chart_types):
            dataset["chartType"] = chart_types[index]
        datasets.append(dataset)

    return {
        "type": chart_type,
        "height": height,
        "colors": colors,
        "data": {
            "labels": ["" if label is None else str(label) for label in labels],
            "datasets": datasets,
        },
        "axisOptions": {"xIsSeries": 1, "shortenYAxisNumbers": 1},
        "barOptions": {"spaceRatio": 0.35, "stacked": 0},
        "maxSlices": 10,
    }


# ---------------------------------------------------------------------------
# Export table
# ---------------------------------------------------------------------------


def _get_export_columns():
    return [
        {"label": _("Section"), "fieldname": "section", "fieldtype": "Data", "width": 150},
        {"label": _("Metric"), "fieldname": "metric", "fieldtype": "Data", "width": 240},
        {"label": _("Value"), "fieldname": "value", "fieldtype": "Data", "width": 180},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Notes"), "fieldname": "notes", "fieldtype": "Data", "width": 420},
    ]


def _build_export_rows(sources, alerts, overall):
    rows = [
        {
            "section": _("Executive"),
            "metric": _("Consolidated Status"),
            "value": overall["label"],
            "status": overall["status"],
            "notes": overall["reason"],
        }
    ]

    def add(section, metric, value, status="BLUE", notes=""):
        rows.append(
            {
                "section": section,
                "metric": metric,
                "value": value,
                "status": status,
                "notes": notes,
            }
        )

    opd = sources["opd"]
    if opd.get("available"):
        t = opd["totals"]
        add("OPD", _("Total Patients"), _number(t["total_cases"]))
        add("OPD", _("New Patients"), _number(t["new_patient"]))
        add("OPD", _("Follow Up"), _number(t["follow_up"]))
        add("OPD", _("Pending Patients"), _number(t["open_cases"]), "RED" if t["open_cases"] else "GREEN")
        add("OPD", _("Completed Patients"), _number(t["closed_cases"]), "GREEN")
    else:
        add("OPD", _("Source"), _("Unavailable"), "GREY", opd.get("reason"))

    queue = sources["queue"]
    if queue.get("available"):
        t = queue["totals"]
        add("Queue Time", _("Patients Queued"), _number(t["patients"]))
        add("Queue Time", _("Encountered"), _number(t["encountered"]), "GREEN")
        add("Queue Time", _("Waiting / No Encounter"), _number(t["waiting"]), queue.get("status"))
        add("Queue Time", _("Average Queue Wait"), _minutes_display(t["average_wait"]), _wait_status(t["average_wait"]))
        add("Queue Time", _("Longest Queue Wait"), _minutes_display(t["longest_wait"]), _wait_status(t["longest_wait"]))
        add("Queue Time", _("Over 20 Minutes"), _number(t["over_target"]), "RED" if t["critical_waits"] else ("AMBER" if t["over_target"] else "GREEN"))
        add("Queue Time", _("Samples Collected"), _number(t["samples_collected"]), "BLUE")
        add("Queue Time", _("Lab Results Available"), _number(t["lab_results"]), "BLUE")
    else:
        add("Queue Time", _("Source"), _("Unavailable"), "GREY", queue.get("reason"))

    ipd = sources["ipd"]
    if ipd.get("available"):
        t = ipd["totals"]
        add("IPD", _("Total Records"), _number(t["total_patients"]))
        add("IPD", _("Admission Scheduled"), _number(t["admission_scheduled"]))
        add("IPD", _("Admitted"), _number(t["admitted"]), "GREEN")
        add("IPD", _("Discharged"), _number(t["discharged"]), "GREEN")
        add("IPD", _("Cancelled"), _number(t["cancelled"]), "RED" if t["cancelled"] else "GREEN")
    else:
        add("IPD", _("Source"), _("Unavailable"), "GREY", ipd.get("reason"))

    ot = sources["ot"]
    if ot.get("available"):
        t = ot["totals"]
        currency = ot.get("currency")
        add("OT", _("OT Cases"), _number(t["cases"]))
        add("OT", _("Unique Patients"), _number(t["patients"]))
        add("OT", _("Total Amount"), _money(t["amount"], currency), "BLUE")
        add("OT", _("Pre-op Ready"), _number(t["preop_ready"]), "GREEN")
        add("OT", _("Pre-op Pending"), _number(t["pending_preop"]), "AMBER" if t["pending_preop"] else "GREEN")
        add("OT", _("Laboratory Tests Done"), _number(t["lab_done"]), "BLUE")
        add("OT", _("IOL Calculation Done"), _number(t["iol_done"]), "BLUE")
        add("OT", _("Consent Forms Signed"), _number(t["consent_done"]), "BLUE")
        add("OT", _("Admissions Done"), _number(t["admission_done"]), "BLUE")
    else:
        add("OT", _("Source"), _("Unavailable"), "GREY", ot.get("reason"))

    lab = sources["lab"]
    if lab.get("available"):
        t = lab["totals"]
        add("Laboratory", _("Lab Result Documents"), _number(t["documents"]))
        add("Laboratory", _("Tests Performed"), _number(t["analytes"]))
        add("Laboratory", _("Normal Results"), _number(t["normal"]), "GREEN")
        add("Laboratory", _("Abnormal Results"), _number(t["abnormal"]), lab.get("status"))
        add("Laboratory", _("Abnormal Rate"), _percent(t["abnormal_rate"]), lab.get("status"))
        add("Laboratory", _("Evaluation Coverage"), _percent(t["evaluation_coverage"]), "BLUE")
    else:
        add("Laboratory", _("Source"), _("Unavailable"), "GREY", lab.get("reason"))

    pharmacy = sources["pharmacy"]
    if pharmacy.get("available"):
        t = pharmacy.get("totals") or {}
        currency = pharmacy.get("currency")
        add("Pharmacy", _("Stockouts"), _optional_number(t.get("stockouts")), "RED" if t.get("stockouts") else "GREEN")
        add("Pharmacy", _("Low Stock"), _optional_number(t.get("low_stock")), "GREY" if t.get("low_stock") is None else ("AMBER" if t.get("low_stock") else "GREEN"))
        add("Pharmacy", _("Near Expiry <90 Days"), _optional_number(t.get("near_expiry")), "GREY" if t.get("near_expiry") is None else ("AMBER" if t.get("near_expiry") else "GREEN"))
        add("Pharmacy", _("Near Expiry Value"), _optional_money(t.get("near_expiry_value"), currency), "AMBER" if t.get("near_expiry") else "GREEN")
        add("Pharmacy", _("Expired Stock"), _optional_number(t.get("expired_stock")), "GREY" if t.get("expired_stock") is None else ("RED" if t.get("expired_stock") else "GREEN"))
        add("Pharmacy", _("Pending POs"), _optional_number(t.get("pending_pos")), "AMBER" if t.get("pending_pos") else "GREEN")
        add("Pharmacy", _("Prescriptions Dispensed"), _optional_number(t.get("prescriptions_dispensed")), "GREY" if t.get("prescriptions_dispensed") is None else "GREEN")
    else:
        add("Pharmacy", _("Source"), _("Unavailable"), "GREY", pharmacy.get("reason"))

    warehouse = sources["warehouse"]
    if warehouse.get("available"):
        t = warehouse.get("totals") or {}
        add("Warehouse", _("Stockouts"), _optional_number(t.get("stockouts")), "RED" if t.get("stockouts") else "GREEN")
        add("Warehouse", _("Low Stock"), _optional_number(t.get("low_stock")), "GREY" if t.get("low_stock") is None else ("AMBER" if t.get("low_stock") else "GREEN"))
        add("Warehouse", _("Critical Drugs Unavailable"), _optional_number(t.get("critical_unavailable")), "GREY" if t.get("critical_unavailable") is None else ("RED" if t.get("critical_unavailable") else "GREEN"))
        add("Warehouse", _("Near Expiry <90 Days"), _optional_number(t.get("near_expiry")), "GREY" if t.get("near_expiry") is None else ("AMBER" if t.get("near_expiry") else "GREEN"))
        add("Warehouse", _("Expired Stock"), _optional_number(t.get("expired_stock")), "GREY" if t.get("expired_stock") is None else ("RED" if t.get("expired_stock") else "GREEN"))
        add("Warehouse", _("Pending POs"), _optional_number(t.get("pending_pos")), "AMBER" if t.get("pending_pos") else "GREEN")
    else:
        add("Warehouse", _("Source"), _("Unavailable"), "GREY", warehouse.get("reason"))

    optical = sources["optical"]
    if optical.get("available"):
        t = optical.get("totals") or {}
        add("Optical", _("Eye Patients Seen"), _optional_number(t.get("eye_patients_seen")), "GREEN")
        add("Optical", _("Patients Prescribed Glasses"), _optional_number(t.get("glasses_prescribed_patients")), "GREY" if t.get("glasses_prescribed_patients") is None else "GREEN")
        add("Optical", _("Glasses Sold"), _optional_number(t.get("glasses_sold"), 2), "GREY" if t.get("glasses_sold") is None else "GREEN")
        add("Optical", _("Frames Sold"), _optional_number(t.get("frames_sold"), 2), "GREY" if t.get("frames_sold") is None else "GREEN")
        add("Optical", _("Pending Glasses Orders"), _optional_number(t.get("pending_glasses_orders")), "AMBER" if t.get("pending_glasses_orders") else "GREEN")
        add("Optical", _("Frame Stockouts"), _optional_number(t.get("frame_stockouts")), "GREY" if t.get("frame_stockouts") is None else ("RED" if t.get("frame_stockouts") else "GREEN"))
    else:
        add("Optical", _("Source"), _("Unavailable"), "GREY", optical.get("reason"))

    staffing = sources["staffing"]
    if staffing.get("available"):
        t = staffing.get("totals") or {}
        add("HR Attendance", _("Present Staff"), _number(t.get("present_staff")), "GREEN")
        add("HR Attendance", _("Absent Staff"), _number(t.get("absent_staff")), "RED" if t.get("absent_staff") else "GREEN")
        add("HR Attendance", _("Not Checked In"), _number(t.get("not_checked_in")), "AMBER" if t.get("not_checked_in") else "GREEN")
        # add("HR Attendance", _("Absent Doctors"), _number(t.get("absent_doctors")), "RED" if t.get("absent_doctors") else "GREEN")
        # add("HR Attendance", _("Absent Nurses"), _number(t.get("absent_nurses")), "RED" if t.get("absent_nurses") else "GREEN")
        add("HR Attendance", _("Departments Affected"), _number(t.get("departments_affected")), "RED" if t.get("departments_affected") else "GREEN")
    else:
        add("HR Attendance", _("Source"), _("Unavailable"), "GREY", staffing.get("reason"))

    hr = sources["hr"]
    if hr.get("available"):
        t = hr["totals"]
        currency = hr.get("currency")
        add("HR", _("Total Employees"), _number(t["employees"]))
        add("HR", _("Active Employees"), _number(t["active"]), "GREEN")
        add("HR", _("Inactive Employees"), _number(t["inactive"]), "BLUE")
        add("HR", _("Suspended Employees"), _number(t["suspended"]), "AMBER" if t["suspended"] else "GREEN")
        add("HR", _("Employees Left"), _number(t["left"]), "BLUE")
        add("HR", _("Departments"), _number(t["departments"]), "BLUE")
        add("HR", _("Total Salary Cost"), _money(t["total_salary_cost"], currency), "BLUE")
        add("HR", _("Average Salary"), _money(t["average_salary"], currency), "BLUE")
    else:
        add("HR", _("Source"), _("Unavailable"), "GREY", hr.get("reason"))

    inventory = sources["inventory"]
    if inventory.get("available"):
        t = inventory["totals"]
        currency = inventory.get("currency")
        add("Inventory", _("Items"), _number(t["items"]))
        add("Inventory", _("Warehouses"), _number(t["warehouses"]))
        add("Inventory", _("Current Quantity"), _number(t["quantity"], 2), "BLUE")
        add("Inventory", _("Stock Value"), _money(t["stock_value"], currency), "GREEN")
        add("Inventory", _("Average Cost"), _money(t["average_cost"], currency), "BLUE")
        add("Inventory", _("Negative Stock Lines"), _number(t["negative_stock"]), "AMBER" if t["negative_stock"] else "GREEN")
        add("Inventory", _("Expired Items"), _number(t["expired_items"]), "AMBER" if t["expired_items"] else "GREEN")
    else:
        add("Inventory", _("Source"), _("Unavailable"), "GREY", inventory.get("reason"))

    sales = sources["sales"]
    if sales.get("available"):
        t = sales["totals"]
        currency = sales.get("currency")
        add("Sales", _("Invoices"), _number(t["invoices"]))
        add("Sales", _("Revenue"), _money(t["revenue"], currency), "GREEN")
        add("Sales", _("Collected"), _money(t["collected"], currency), "GREEN")
        add("Sales", _("Outstanding"), _money(t["outstanding"], currency), "AMBER" if t["outstanding"] else "GREEN")
        add("Sales", _("Overdue Invoices"), _number(t["overdue_invoices"]), "RED" if t["overdue_invoices"] else "GREEN")
        add("Sales", _("Payments Received"), _money(t["payments_received"], currency), "BLUE")
    else:
        add("Sales", _("Source"), _("Unavailable"), "GREY", sales.get("reason"))

    finance = sources["finance"]
    if finance.get("available"):
        t = finance["totals"]
        currency = finance.get("currency")
        add("Finance", _("Income"), _money(t["income"], currency), "GREEN")
        add("Finance", _("Expense"), _money(t["expense"], currency), "BLUE")
        add("Finance", _("Net Profit"), _money(t["net_profit"], currency), "RED" if t["net_profit"] < 0 else "GREEN")
        add("Finance", _("Receivables"), _money(t["receivable"], currency), "AMBER" if t["receivable"] else "GREEN")
        add("Finance", _("Payables"), _money(t["payable"], currency), "BLUE")
    else:
        add("Finance", _("Source"), _("Unavailable"), "GREY", finance.get("reason"))

    for alert in alerts:
        add(
            _("Action Required"),
            alert["title"],
            alert["domain"],
            alert["severity"],
            alert["detail"],
        )

    return rows


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------


def _render_dashboard(filters, sources, alerts, notices, overall, cards, payload):
    """Render the visual report through a real Jinja/HTML template.

    Keeping the shell in ``main_hospital_dashbo.html`` makes the
    dashboard easier to redesign without editing the reporting calculations.
    The detailed cards, tables, and charts remain generated by the existing
    rendering helpers and are passed to the template as trusted, escaped HTML.
    """
    generated = now_datetime().strftime("%Y-%m-%d %H:%M")
    period = "{0} → {1}".format(_e(filters.from_date), _e(filters.to_date))

    context = {
        "payload_json": _json_for_html(payload),
        "title": _e(DASHBOARD_TITLE),
        "period": period,
        "queue_date": _e(filters.get("queue_date") or filters.to_date),
        "generated": _e(generated),
        "overall_class": _e(overall["status"].lower()),
        "overall_status": _e(overall["status"]),
        "overall_label": _e(overall["label"]),
        "overall_reason": _e(overall["reason"]),
        "notice_html": _render_notice_panel(notices),
        "card_html": "".join(_render_kpi_card(card) for card in cards),
        "alert_html": _render_alert_panel(alerts),
        "sections_html": "".join(
            [
                _render_overview_section(sources, alerts),
                _render_opd_section(sources["opd"]),
                _render_queue_section(sources["queue"]),
                _render_ipd_section(sources["ipd"]),
                _render_ot_section(sources["ot"]),
                _render_lab_section(sources["lab"]),
                _render_pharmacy_section(sources["pharmacy"]),
                _render_warehouse_section(sources["warehouse"]),
                _render_optical_section(sources["optical"]),
                _render_hr_section(sources["hr"], sources["staffing"]),
                _render_inventory_section(sources["inventory"]),
                _render_sales_section(sources["sales"]),
                _render_finance_section(sources["finance"]),
            ]
        ),
    }

    template_path = Path(__file__).with_name("main_hospital_dashbo.html")
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        frappe.log_error(frappe.get_traceback(), "{0}: HTML template".format(REPORT_NAME))
        frappe.throw(
            _("Dashboard HTML template could not be loaded: {0}").format(exc)
        )

    return frappe.render_template(template, context)

def _render_kpi_card(card):
    action = ""
    if card.get("tab"):
        action = ' data-hcc-open-tab="{0}" role="button" tabindex="0"'.format(_e(card["tab"]))
    return """
    <article class="hcc-kpi hcc-status-{status}"{action}>
        <div class="hcc-kpi-label">{label}</div>
        <div class="hcc-kpi-value">{value}</div>
        <div class="hcc-kpi-foot"><span>{subtext}</span><span class="hcc-dot"></span></div>
    </article>
    """.format(
        status=_e(card.get("status", "BLUE").lower()),
        action=action,
        label=_e(card.get("label")),
        value=_e(card.get("display")),
        subtext=_e(card.get("subtext")),
    )


def _render_notice_panel(notices):
    if not notices:
        return ""
    items = "".join(
        "<li><strong>{0}:</strong> {1}</li>".format(_e(item["source"]), _e(item["message"]))
        for item in notices
    )
    return """
    <details class="hcc-notices">
        <summary>Data coverage and assumptions ({count})</summary>
        <ul>{items}</ul>
    </details>
    """.format(count=len(notices), items=items)


def _render_alert_panel(alerts):
    if not alerts:
        body = """
        <div class="hcc-empty hcc-empty-success">
            <strong>No configured red or amber exceptions</strong>
            <span>This does not replace the full hospital-readiness, equipment, staffing or waiting-time controls.</span>
        </div>
        """
    else:
        rows = []
        for alert in alerts:
            action = ""
            if alert.get("report"):
                action = (
                    '<button type="button" class="hcc-link-button" '
                    'data-hcc-report="{0}">Open source</button>'
                ).format(_e(alert["report"]))
            rows.append(
                """
                <div class="hcc-alert-row hcc-status-{severity}">
                    <span class="hcc-alert-severity">{severity}</span>
                    <div class="hcc-alert-copy">
                        <strong>{title}</strong>
                        <span>{detail}</span>
                    </div>
                    <span class="hcc-alert-domain">{domain}</span>
                    {action}
                </div>
                """.format(
                    severity=_e(alert["severity"].lower()),
                    title=_e(alert["title"]),
                    detail=_e(alert["detail"]),
                    domain=_e(alert["domain"]),
                    action=action,
                )
            )
        body = "".join(rows)

    return """
    <section class="hcc-panel hcc-action-panel">
        <div class="hcc-panel-head">
            <div>
                <span class="hcc-section-kicker">MANAGEMENT ACTION</span>
                <h2>Exceptions requiring attention</h2>
            </div>
            <span class="hcc-count-badge">{count}</span>
        </div>
        <div class="hcc-alert-list">{body}</div>
    </section>
    """.format(count=len(alerts), body=body)


def _render_overview_section(sources, alerts):
    source_cards = []
    definitions = [
        ("opd", "OPD", "Patient volume, type and pending/completed queue status", "opd"),
        ("queue", "Queue Time", "Queue-to-encounter, collection and result timestamps", "queue"),
        ("ipd", "IPD", "Admission, discharge and cancellation status", "ipd"),
        ("ot", "OT", "Operations, pre-operative readiness and procedure activity", "ot"),
        ("lab", "Laboratory", "Blood analytes, abnormal rate and test volume", "lab"),
        ("pharmacy", "Pharmacy", "Stockouts, low stock, expiry, purchase orders and dispensing", "pharmacy"),
        ("warehouse", "Warehouse", "Enabled-warehouse stock exceptions, critical drugs and pending POs", "warehouse"),
        ("optical", "Optical", "Eye patients, glasses prescriptions, sales, orders and frame stock", "optical"),
        ("hr", "HR", "Employee master, salary assignment and Employee Checkin attendance", "hr"),
        ("inventory", "Inventory", "Warehouse-grouped quantity, value and stock exceptions", "inventory"),
        ("sales", "Sales", "Submitted invoices, collections and outstanding", "sales"),
        ("finance", "Financial", "GL P&L plus receivable and payable balances", "finance"),
    ]

    for key, title, description, tab in definitions:
        source = sources[key]
        if key == "hr":
            source = dict(source)
            source["attendance"] = sources.get("staffing") or {}
            source["available"] = bool(source.get("available") or source["attendance"].get("available"))
            source["status"] = max(
                (source.get("status", "GREY"), source["attendance"].get("status", "GREY")),
                key=_status_rank,
            )
        status = _source_status(source)
        if source.get("available"):
            value = _overview_value(key, source)
            reason = _overview_reason(key, source)
        else:
            value = _("Unavailable")
            reason = source.get("reason") or _("Source unavailable")

        report_button = ""
        if source.get("report_exists"):
            report_button = (
                '<button type="button" class="hcc-link-button" data-hcc-report="{0}">Source report</button>'
            ).format(_e(source.get("report")))

        source_cards.append(
            """
            <article class="hcc-source-card hcc-status-{status}">
                <div class="hcc-source-top">
                    <span class="hcc-source-name">{title}</span>
                    <span class="hcc-status-pill">{status_upper}</span>
                </div>
                <strong>{value}</strong>
                <p>{description}</p>
                <small>{reason}</small>
                <div class="hcc-source-actions">
                    <button type="button" class="hcc-link-button" data-hcc-open-tab="{tab}">Go to section</button>
                    {report_button}
                </div>
            </article>
            """.format(
                status=_e(status.lower()),
                title=_e(title),
                status_upper=_e(status),
                value=_e(value),
                description=_e(description),
                reason=_e(reason),
                tab=_e(tab),
                report_button=report_button,
            )
        )

    red = sum(1 for item in alerts if item["severity"] == "RED")
    amber = sum(1 for item in alerts if item["severity"] == "AMBER")

    return """
    <section id="hcc-section-overview" class="hcc-section hcc-section-overview" data-hcc-section="overview">
        <div class="hcc-section-heading">
            <div>
                <span class="hcc-section-kicker">EXECUTIVE OVERVIEW</span>
                <h2>Twelve operational sections in one management view</h2>
                <p>Each source report keeps its own calculations, cards, charts and detail tables.</p>
            </div>
            <div class="hcc-mini-summary">
                <span><strong>{red}</strong> red</span>
                <span><strong>{amber}</strong> amber</span>
            </div>
        </div>
        <div class="hcc-source-grid">{cards}</div>
        <div class="hcc-boundary-note">
            <strong>Version 1 scope:</strong>
            This dashboard consolidates OPD, Queue Time, IPD, OT Form, Blood laboratory, Pharmacy, Warehouse, Optical, HR with Employee Checkin attendance, Inventory, Sales and Finance.
            Stock exceptions follow Pharmacy→Drug, Optical→Optical and Supermarket→Supermarket mappings. Employee Checkin attendance is merged into HR and any accepted punch is Present.
        </div>
    </section>
    """.format(red=red, amber=amber, cards="".join(source_cards))


def _overview_value(key, source):
    totals = source.get("totals") or {}
    if key == "opd":
        return _("{0} patients").format(_number(totals.get("total_cases")))
    if key == "queue":
        return _("{0} queued").format(_number(totals.get("patients")))
    if key == "ipd":
        return _("{0} records").format(_number(totals.get("total_patients")))
    if key == "ot":
        return _("{0} cases").format(_number(totals.get("cases")))
    if key == "lab":
        return _("{0} tests").format(_number(totals.get("analytes")))
    if key == "pharmacy":
        return _("{0} stockouts").format(_optional_number(totals.get("stockouts")))
    if key == "warehouse":
        return _("{0} stockouts").format(_optional_number(totals.get("stockouts")))
    if key == "optical":
        return _("{0} eye patients").format(_optional_number(totals.get("eye_patients_seen")))
    if key == "hr":
        attendance = source.get("attendance") or {}
        attendance_totals = attendance.get("totals") or {}
        if totals.get("employees") is not None:
            return _("{0} employees").format(_number(totals.get("employees")))
        return _("{0} staff scheduled").format(_number(attendance_totals.get("total_staff")))
    if key == "inventory":
        return _money(totals.get("stock_value"), source.get("currency"))
    if key == "sales":
        return _money(totals.get("revenue"), source.get("currency"))
    if key == "finance":
        return _money(totals.get("net_profit"), source.get("currency"))
    return "—"


def _overview_reason(key, source):
    totals = source.get("totals") or {}
    if key == "opd":
        return _("{0} pending").format(_number(totals.get("open_cases")))
    if key == "queue":
        return _("{0} waiting · longest {1}").format(
            _number(totals.get("waiting")),
            _minutes_display(totals.get("longest_wait")),
        )
    if key == "ipd":
        return _("{0} cancelled").format(_number(totals.get("cancelled")))
    if key == "ot":
        return _("{0} pre-op pending").format(_number(totals.get("pending_preop")))
    if key == "lab":
        return _("{0} abnormal").format(_percent(totals.get("abnormal_rate")))
    if key == "pharmacy":
        return _("{0} low stock · {1} expired").format(
            _optional_number(totals.get("low_stock")),
            _optional_number(totals.get("expired_stock")),
        )
    if key == "warehouse":
        return _("{0} critical unavailable · {1} pending POs").format(
            _optional_number(totals.get("critical_unavailable")),
            _optional_number(totals.get("pending_pos")),
        )
    if key == "optical":
        return _("{0} pending orders · {1} frame stockouts").format(
            _optional_number(totals.get("pending_glasses_orders")),
            _optional_number(totals.get("frame_stockouts")),
        )
    if key == "hr":
        attendance = source.get("attendance") or {}
        attendance_totals = attendance.get("totals") or {}
        return _("{0} active · {1} present · {2} absent").format(
            _number(totals.get("active")),
            _number(attendance_totals.get("present_staff")),
            _number(attendance_totals.get("absent_staff")),
        )
    if key == "inventory":
        return _("{0} items · {1} warehouses").format(
            _number(totals.get("items")),
            _number(totals.get("warehouses")),
        )
    if key == "sales":
        return _("{0} outstanding").format(_money(totals.get("outstanding"), source.get("currency")))
    if key == "finance":
        return _("P&L for period; balances as of To Date")
    return ""


def _render_opd_section(opd):
    if not opd.get("available"):
        return _render_unavailable_section("opd", "OPD", opd)

    t = opd["totals"]
    metrics = [
        (_("Total OPD Patients"), _number(t["total_cases"]), "BLUE"),
        (_("New Patients"), _number(t["new_patient"]), "BLUE"),
        (_("Follow Up"), _number(t["follow_up"]), "AMBER"),
        (_("Refer"), _number(t["refer"]), "BLUE"),
        (_("Revisit"), _number(t["revisit"]), "BLUE"),
        (_("Pending Patients"), _number(t["open_cases"]), "RED" if t["open_cases"] else "GREEN"),
        (_("Completed Patients"), _number(t["closed_cases"]), "GREEN"),
    ]

    doctor_rows = [
        [
            row.get("practitioner"),
            row.get("department"),
            _number(row.get("new_patient")),
            _number(row.get("follow_up")),
            _number(row.get("refer")),
            _number(row.get("open_cases")),
            _number(row.get("total_cases")),
        ]
        for row in opd.get("by_doctor") or []
    ]

    return _section_shell(
        "opd",
        "OPD REPORT",
        "OPD Report",
        "Counts come from Que and retain the original New Patient, Follow Up, Refer, Revisit, Open and Closed categories.",
        OPD_REPORT if opd.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("opd_mix", "Patient type mix", "New, follow-up, referral and revisit"),
                ("opd_status", "Queue completion", "Open versus Closed"),
                ("opd_trend", "Daily OPD trend", "Total volume and pending records"),
                ("opd_doctor", "Practitioner activity", "Top practitioners by total cases"),
            ]
        )
        + _table_card(
            "Top OPD practitioners",
            ["Practitioner", "Department", "New", "Follow Up", "Refer", "Pending", "Total"],
            doctor_rows,
        ),
    )


def _render_queue_section(queue):
    if not queue.get("available"):
        return _render_unavailable_section("queue", "Queue Time Management", queue)

    t = queue["totals"]
    metrics = [
        (_("Patients Queued"), _number(t["patients"]), "BLUE"),
        (_("Encountered"), _number(t["encountered"]), "GREEN"),
        (_("Waiting / No Encounter"), _number(t["waiting"]), queue.get("status", "GREY") if t["waiting"] else "GREEN"),
        (_("Average Queue Wait"), _minutes_display(t["average_wait"]), _wait_status(t["average_wait"])),
        (_("Longest Queue Wait"), _minutes_display(t["longest_wait"]), _wait_status(t["longest_wait"])),
        (_("Over 20 Minutes"), _number(t["over_target"]), "RED" if t["critical_waits"] else ("AMBER" if t["over_target"] else "GREEN")),
        (_("Samples Collected"), _number(t["samples_collected"]), "BLUE"),
        (_("Lab Results Available"), _number(t["lab_results"]), "BLUE"),
        (_("Avg Encounter to Collection"), _minutes_display(t["average_encounter_to_collection"]), "BLUE"),
        (_("Avg Collection to Result"), _minutes_display(t["average_collection_to_result"]), "BLUE"),
    ]

    journey_rows = [
        [
            row.get("date"),
            row.get("patient"),
            row.get("patient_name"),
            row.get("practitioner") or "—",
            row.get("queue_time"),
            row.get("encounter_time"),
            row.get("sample_collection_time"),
            row.get("lab_result_time"),
            row.get("queue_to_encounter"),
            row.get("encounter_to_collection"),
            row.get("collection_to_result"),
            row.get("queue_status"),
        ]
        for row in queue.get("recent") or []
    ]

    doctor_rows = [
        [
            row.get("practitioner"),
            _number(row.get("patients")),
            _number(row.get("encountered")),
            _number(row.get("waiting")),
            _minutes_display(row.get("average_wait")),
            _minutes_display(row.get("longest_wait")),
            _number(row.get("over_target")),
        ]
        for row in queue.get("by_doctor") or []
    ]

    return _section_shell(
        "queue",
        "QUEUE TIME MANAGEMENT",
        "Queue Time Management",
        _("One patient journey per queue date. The first chronological encounter, sample collection and lab result after the previous stage are used. Target: queue to encounter ≤20 minutes; critical when >30 minutes."),
        QUEUE_REPORT if queue.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("queue_stages", "Patient journey completion", "Queue, encounter, sample collection and lab-result stages"),
                ("queue_wait_status", "Queue wait distribution", "Within target, delayed, critical and no encounter"),
                ("queue_hourly", "Hourly queue flow", "Queue arrivals and encounters by hour"),
                ("queue_doctor_wait", "Wait time by doctor", "Average and longest queue-to-encounter wait"),
            ]
        )
        + _table_card(
            "Patient journey timestamps",
            [
                "Date",
                "Patient",
                "Patient Name",
                "Doctor",
                "Queue Time",
                "Encounter Time",
                "Sample Collection",
                "Lab Result",
                "Queue → Encounter",
                "Encounter → Collection",
                "Collection → Result",
                "Status",
            ],
            journey_rows,
        )
        + _table_card(
            "Queue performance by doctor",
            ["Doctor", "Patients", "Encountered", "Waiting", "Average Wait", "Longest Wait", "Over Target"],
            doctor_rows,
        ),
    )


def _render_ipd_section(ipd):
    if not ipd.get("available"):
        return _render_unavailable_section("ipd", "IPD", ipd)

    t = ipd["totals"]
    metrics = [
        (_("Total Doctors"), _number(t["total_doctors"]), "BLUE"),
        (_("Total IPD Patients"), _number(t["total_patients"]), "BLUE"),
        (_("Admission Scheduled"), _number(t["admission_scheduled"]), "AMBER"),
        (_("Admitted"), _number(t["admitted"]), "BLUE"),
        (_("Discharge Scheduled"), _number(t["discharge_scheduled"]), "AMBER"),
        (_("Discharged"), _number(t["discharged"]), "GREEN"),
        (_("Cancelled"), _number(t["cancelled"]), "RED" if t["cancelled"] else "GREEN"),
    ]

    doctor_rows = [
        [
            row.get("practitioner"),
            row.get("department"),
            _number(row.get("admission_scheduled")),
            _number(row.get("admitted")),
            _number(row.get("discharge_scheduled")),
            _number(row.get("discharged")),
            _number(row.get("cancelled")),
            _number(row.get("total_patients")),
        ]
        for row in ipd.get("by_doctor") or []
    ]

    return _section_shell(
        "ipd",
        "IPD REPORT",
        "IPD Report",
        "Counts follow Inpatient Record status and are grouped by admission practitioner and department.",
        IPD_REPORT if ipd.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("ipd_status", "IPD status distribution", "Admission through discharge"),
                ("ipd_trend", "Daily IPD trend", "Created records and cancellations"),
                ("ipd_doctor", "IPD by doctor", "Admitted, discharged and cancelled"),
            ]
        )
        + _table_card(
            "IPD by practitioner",
            [
                "Practitioner",
                "Department",
                "Admission Scheduled",
                "Admitted",
                "Discharge Scheduled",
                "Discharged",
                "Cancelled",
                "Total",
            ],
            doctor_rows,
        ),
    )



def _render_ot_section(ot):
    if not ot.get("available"):
        return _render_unavailable_section("ot", "OT Form", ot)

    t = ot["totals"]
    currency = ot.get("currency")
    metrics = [
        (_("OT Cases"), _number(t["cases"]), "BLUE"),
        (_("Unique Patients"), _number(t["patients"]), "BLUE"),
        (_("Total Amount"), _money(t["amount"], currency), "BLUE"),
        (_("Pre-op Ready"), _number(t["preop_ready"]), "GREEN"),
        (_("Pre-op Pending"), _number(t["pending_preop"]), "AMBER" if t["pending_preop"] else "GREEN"),
        (_("Laboratory Tests Done"), _number(t["lab_done"]), "GREEN"),
        (_("IOL Calculation Done"), _number(t["iol_done"]), "GREEN"),
        (_("Consent Forms Signed"), _number(t["consent_done"]), "GREEN"),
        (_("Admissions Done"), _number(t["admission_done"]), "GREEN"),
    ]

    recent_rows = []
    for row in ot.get("recent") or []:
        age = " ".join(
            str(value).strip()
            for value in (row.get("patient_age"), row.get("age_type"))
            if value not in (None, "")
        ) or "—"
        readiness = _("Ready") if row.get("preop_ready") else _("Pending: {0}").format(
            ", ".join(row.get("pending_requirements") or [])
        )
        recent_rows.append(
            [
                _safe_date_label(row.get("operation_date")),
                row.get("patient_name") or row.get("patient"),
                row.get("mobile_no") or "—",
                age,
                row.get("sex") or "—",
                row.get("doctor") or "—",
                row.get("procedure_name") or "—",
                row.get("eye_side") or "—",
                _money(row.get("amount"), currency),
                readiness,
                row.get("call_status") or "—",
            ]
        )

    call_rows = [
        [row.get("label"), _number(row.get("value"))]
        for row in ot.get("call_statuses") or []
    ]

    return _section_shell(
        "ot",
        "OT FORM REPORT",
        "OT Form Report",
        "Operations are read from OT Form and patient demographics are joined from Patient. Pre-op readiness requires laboratory tests, IOL calculation, consent and admission to be completed.",
        OT_REPORT if ot.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("ot_trend", "OT activity trend", "Cases and amount by operation date"),
                ("ot_readiness", "Pre-operative completion", "Completed requirements across OT cases"),
                ("ot_procedures", "Top procedures", "Cases by procedure name"),
                ("ot_eye_side", "Eye-side distribution", "OT cases by eye side"),
            ]
        )
        + _table_card(
            "Recent OT cases",
            [
                "Date",
                "Patient Name",
                "Mobile No",
                "Age",
                "Sex",
                "Doctor",
                "Procedure",
                "Eye Side",
                "Amount",
                "Pre-op",
                "Call Status",
            ],
            recent_rows,
        )
        + _table_card(
            "Call status summary",
            ["Call Status", "Cases"],
            call_rows,
        ),
    )


def _render_lab_section(lab):
    if not lab.get("available"):
        return _render_unavailable_section("lab", "Laboratory", lab)

    t = lab["totals"]
    metrics = [
        (_("Tests Performed"), _number(t["analytes"]), "BLUE"),
        (_("Lab Results"), _number(t["documents"]), "BLUE"),
        (_("Patients"), _number(t["patients"]), "BLUE"),
        (_("Completed Results"), _number(t["documents"]), "GREEN"),
        (_("Normal Results"), _number(t["normal"]), "GREEN"),
        (_("Abnormal Results"), _number(t["abnormal"]), lab.get("status")),
        (_("Abnormal Rate"), _percent(t["abnormal_rate"]), lab.get("status")),
        (_("Not Evaluated"), _number(t["not_evaluated"]), "AMBER" if t["not_evaluated"] else "GREEN"),
        (_("Evaluation Coverage"), _percent(t["evaluation_coverage"]), "BLUE"),
    ]

    test_rows = [
        [index + 1, row.get("label"), _number(row.get("value"))]
        for index, row in enumerate(lab.get("top_tests") or [])
    ]

    department_rows = [
        [
            row.get("department"),
            _number(row.get("normal")),
            _number(row.get("abnormal")),
            _number(row.get("total")),
        ]
        for row in lab.get("by_department") or []
    ]

    return _section_shell(
        "lab",
        "LAB RESULT ANALYTICS",
        "Laboratory Report",
        "Document counts are de-duplicated by Lab Result; test volume and abnormality are counted per analyte row.",
        LAB_REPORT if lab.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("lab_mix", "Result distribution", "Normal, abnormal and not evaluated analytes"),
                ("lab_top_tests", "Most ordered tests", "Analyte row count"),
                ("lab_trend", "Daily result trend", "Normal versus abnormal"),
                ("lab_department", "Results by department", "Evaluated analytes only"),
            ]
        )
        + '<div class="hcc-grid hcc-grid-2">'
        + _table_card("Most ordered tests", ["#", "Test", "Results"], test_rows, nested=True)
        + _table_card(
            "Normal vs abnormal by department",
            ["Department", "Normal", "Abnormal", "Total"],
            department_rows,
            nested=True,
        )
        + "</div>",
    )



def _metric_status(value, warning_status="AMBER", zero_status="GREEN"):
    if value is None:
        return "GREY"
    return warning_status if flt(value) else zero_status


def _render_stock_exception_rows(source):
    currency = source.get("currency")
    return [
        [
            row.get("exception_type") or "—",
            row.get("warehouse") or "—",
            row.get("item_code") or "—",
            row.get("item_name") or row.get("item_code") or "—",
            row.get("item_group") or "—",
            _number(row.get("actual_qty"), 2),
            _optional_number(row.get("reorder_level"), 2),
            _safe_date_label(row.get("expiry_date")),
            _money(row.get("stock_value"), currency),
            _("Yes") if cint(row.get("is_critical")) else _("No"),
        ]
        for row in source.get("exceptions") or []
    ]


def _render_po_rows(source):
    currency = source.get("currency")
    return [
        [
            row.get("name") or "—",
            row.get("supplier") or "—",
            _safe_date_label(row.get("transaction_date")),
            row.get("status") or "—",
            _money(row.get("total"), currency),
        ]
        for row in source.get("pending_pos") or []
    ]


def _render_pharmacy_section(pharmacy):
    if not pharmacy.get("available"):
        return _render_unavailable_section("pharmacy", "Pharmacy Report", pharmacy)

    t = pharmacy.get("totals") or {}
    currency = pharmacy.get("currency")
    metrics = [
        (_("Stockouts"), _optional_number(t.get("stockouts")), _metric_status(t.get("stockouts"), "RED")),
        (_("Low Stock"), _optional_number(t.get("low_stock")), _metric_status(t.get("low_stock"))),
        (_("Near Expiry <90 Days"), _optional_number(t.get("near_expiry")), _metric_status(t.get("near_expiry"))),
        (_("Near Expiry Value"), _optional_money(t.get("near_expiry_value"), currency), _metric_status(t.get("near_expiry"))),
        (_("Expired Stock"), _optional_number(t.get("expired_stock")), _metric_status(t.get("expired_stock"), "RED")),
        (_("Expired Stock Value"), _optional_money(t.get("expired_stock_value"), currency), _metric_status(t.get("expired_stock"), "RED")),
        (_("Pending POs"), _optional_number(t.get("pending_pos")), _metric_status(t.get("pending_pos"))),
        (_("Prescriptions Dispensed"), _optional_number(t.get("prescriptions_dispensed")), "GREY" if t.get("prescriptions_dispensed") is None else "GREEN"),
    ]

    return _section_shell(
        "pharmacy",
        "PHARMACY REPORT · 8:00 AM REVIEW",
        "Pharmacy Report",
        "Enabled Pharmacy warehouses paired with Item Group Drug. Stockout means zero/negative quantity or an expired Item date; low stock excludes expired quantity.",
        INVENTORY_REPORT if pharmacy.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("pharmacy_exceptions", "Pharmacy stock exceptions", "Stockouts, low stock, near expiry and expired items"),
                ("pharmacy_warehouse", "Exceptions by pharmacy warehouse", "Enabled warehouse scope"),
            ]
        )
        + _table_card(
            "Pharmacy stock exceptions",
            ["Exception", "Warehouse", "Item", "Item Name", "Item Group", "Qty", "Reorder Level", "Expiry", "Stock Value", "Critical"],
            _render_stock_exception_rows(pharmacy),
        )
        + _table_card(
            "Pending pharmacy purchase orders",
            ["Purchase Order", "Supplier", "Date", "Status", "Total"],
            _render_po_rows(pharmacy),
        ),
    )


def _render_warehouse_section(warehouse):
    if not warehouse.get("available"):
        return _render_unavailable_section("warehouse", "Warehouse Report", warehouse)

    t = warehouse.get("totals") or {}
    currency = warehouse.get("currency")
    metrics = [
        (_("Stockouts"), _optional_number(t.get("stockouts")), _metric_status(t.get("stockouts"), "RED")),
        (_("Low Stock"), _optional_number(t.get("low_stock")), _metric_status(t.get("low_stock"))),
        (_("Critical Drugs Unavailable"), _optional_number(t.get("critical_unavailable")), _metric_status(t.get("critical_unavailable"), "RED")),
        (_("Near Expiry <90 Days"), _optional_number(t.get("near_expiry")), _metric_status(t.get("near_expiry"))),
        (_("Near Expiry Value"), _optional_money(t.get("near_expiry_value"), currency), _metric_status(t.get("near_expiry"))),
        (_("Expired Stock"), _optional_number(t.get("expired_stock")), _metric_status(t.get("expired_stock"), "RED")),
        (_("Expired Stock Value"), _optional_money(t.get("expired_stock_value"), currency), _metric_status(t.get("expired_stock"), "RED")),
        (_("Pending POs"), _optional_number(t.get("pending_pos")), _metric_status(t.get("pending_pos"))),
    ]

    warehouse_rows = [
        [
            row.get("warehouse") or "—",
            row.get("item_groups") or "—",
            _number(row.get("stockouts")),
            _optional_number(row.get("low_stock")),
            _optional_number(row.get("near_expiry")),
            _optional_number(row.get("expired_stock")),
        ]
        for row in warehouse.get("by_warehouse") or []
    ]

    return _section_shell(
        "warehouse",
        "WAREHOUSE REPORT · 8:00 AM REVIEW",
        "Warehouse Report",
        "Daily exception view for enabled mapped warehouses only: Pharmacy→Drug, Optical→Optical and Supermarket→Supermarket. Expired items count as unavailable stock. Pending POs use docstatus = 1 and status != Completed.",
        INVENTORY_REPORT if warehouse.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("warehouse_exceptions", "Warehouse exception totals", "Stockouts, low stock, critical items and expiry"),
                ("warehouse_by_location", "Exceptions by warehouse", "Enabled warehouses only"),
            ]
        )
        + '<div class="hcc-grid hcc-grid-2">'
        + _table_card(
            "Warehouse exception summary",
            ["Warehouse", "Item Group(s)", "Stockouts", "Low Stock", "Near Expiry", "Expired"],
            warehouse_rows,
            nested=True,
        )
        + _table_card(
            "Pending warehouse purchase orders",
            ["Purchase Order", "Supplier", "Date", "Status", "Total"],
            _render_po_rows(warehouse),
            nested=True,
        )
        + "</div>"
        + _table_card(
            "Warehouse stock exceptions",
            ["Exception", "Warehouse", "Item", "Item Name", "Item Group", "Qty", "Reorder Level", "Expiry", "Stock Value", "Critical"],
            _render_stock_exception_rows(warehouse),
        ),
    )


def _render_optical_section(optical):
    if not optical.get("available"):
        return _render_unavailable_section("optical", "Optical Department", optical)

    t = optical.get("totals") or {}
    metrics = [
        (_("Eye Patients Seen"), _optional_number(t.get("eye_patients_seen")), "GREY" if t.get("eye_patients_seen") is None else "GREEN"),
        (_("Patients Prescribed Glasses"), _optional_number(t.get("glasses_prescribed_patients")), "GREY" if t.get("glasses_prescribed_patients") is None else "GREEN"),
        (_("Glasses Sold"), _optional_number(t.get("glasses_sold"), 2), "GREY" if t.get("glasses_sold") is None else "GREEN"),
        (_("Frames Sold"), _optional_number(t.get("frames_sold"), 2), "GREY" if t.get("frames_sold") is None else "GREEN"),
        (_("Pending Glasses Orders"), _optional_number(t.get("pending_glasses_orders")), _metric_status(t.get("pending_glasses_orders"))),
        (_("Pending Glasses Qty"), _optional_number(t.get("pending_glasses_qty"), 2), _metric_status(t.get("pending_glasses_orders"))),
        (_("Frame Stockouts"), _optional_number(t.get("frame_stockouts")), _metric_status(t.get("frame_stockouts"), "RED")),
    ]

    prescription_rows = [
        [
            row.get("name") or "—",
            _safe_date_label(row.get("prescription_date")),
            row.get("patient") or "—",
            row.get("patient_name") or "—",
            row.get("docstatus"),
        ]
        for row in optical.get("recent_prescriptions") or []
    ]
    stockout_rows = [
        [
            row.get("warehouse") or "—",
            row.get("item_code") or "—",
            row.get("item_name") or row.get("item_code") or "—",
            _number(row.get("actual_qty"), 2),
            _safe_date_label(row.get("expiry_date")),
            row.get("stockout_reason") or "—",
        ]
        for row in optical.get("frame_stockouts") or []
    ]

    return _section_shell(
        "optical",
        "OPTICAL DEPARTMENT",
        "Optical Department Report",
        "Eye Patients Seen reads tabQue where department = Opthalmology. EyeGlass Prescription excludes docstatus 2. Frame stockouts use enabled Optical warehouses, Item Group Optical, and expiry-aware availability.",
        None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("optical_activity", "Optical activity", "Patients, prescriptions, sales, pending orders and frame stockouts"),
                ("optical_trend", "Eye patients seen by day", "Distinct Que patients in Opthalmology"),
            ]
        )
        + '<div class="hcc-grid hcc-grid-2">'
        + _table_card(
            "Recent EyeGlass prescriptions",
            ["Prescription", "Date", "Patient", "Patient Name", "Docstatus"],
            prescription_rows,
            nested=True,
        )
        + _table_card(
            "Frame stockouts",
            ["Warehouse", "Item", "Item Name", "Qty", "Expiry", "Reason"],
            stockout_rows,
            nested=True,
        )
        + "</div>",
    )


def _render_hr_section(hr, staffing):
    if not hr.get("available") and not staffing.get("available"):
        reason = hr.get("reason") or staffing.get("reason") or _("HR data is unavailable.")
        return _render_unavailable_section("hr", "Human Resources", {"reason": reason})

    t = hr.get("totals") or {}
    currency = hr.get("currency")
    attendance = staffing.get("totals") or {}
    metrics = []
    if hr.get("available"):
        metrics.extend(
            [
                (_("Total Employees"), _number(t.get("employees")), "BLUE"),
                (_("Active Employees"), _number(t.get("active")), "GREEN"),
                (_("Total Salary Cost"), _money(t.get("total_salary_cost"), currency), "BLUE"),
                (_("Average Salary"), _money(t.get("average_salary"), currency), "BLUE"),
            ]
        )
    if staffing.get("available"):
        metrics.extend(
            [
                (_("Present Staff"), _number(attendance.get("present_staff")), "GREEN"),
                (_("Absent Staff"), _number(attendance.get("absent_staff")), "RED" if attendance.get("absent_staff") else "GREEN"),
                (_("Not Checked In"), _number(attendance.get("not_checked_in")), "AMBER" if attendance.get("not_checked_in") else "GREEN"),
                (_("Attendance Rate"), _percent(attendance.get("attendance_rate")), staffing.get("status", "GREY")),
                # (_("Absent Doctors"), _number(attendance.get("absent_doctors")), "RED" if attendance.get("absent_doctors") else "GREEN"),
                # (_("Absent Nurses"), _number(attendance.get("absent_nurses")), "RED" if attendance.get("absent_nurses") else "GREEN"),
                (_("On Leave"), _number(attendance.get("on_leave")), "BLUE"),
                (_("Holiday"), _number(attendance.get("holiday")), "BLUE"),
            ]
        )

    department_rows = [
        [
            row.get("department"),
            _number(row.get("employees")),
            _number(row.get("active")),
            _number(row.get("inactive")),
            _money(row.get("salary_cost"), currency),
        ]
        for row in hr.get("by_department") or []
    ]
    attendance_department_rows = [
        [
            row.get("department") or "—",
            _number(row.get("total_staff")),
            _number(row.get("present_staff")),
            _number(row.get("absent_staff")),
            _number(row.get("not_checked_in")),
           
        ]
        for row in staffing.get("by_department") or []
    ]
    attendance_exception_rows = [
        [
            row.get("employee") or "—",
            row.get("employee_name") or row.get("employee") or "—",
            row.get("department") or "—",
            row.get("designation") or "—",
            # row.get("staff_type") or "Staff",
            row.get("status") or "—",
            str(row.get("first_checkin") or "—"),
            str(row.get("last_checkin") or "—"),
        ]
        for row in staffing.get("attendance_exceptions") or []
    ]
    recent_rows = [
        [
            row.get("employee"),
            row.get("employee_name"),
            row.get("department") or "—",
            row.get("designation") or "—",
            row.get("employment_type") or "—",
            row.get("status") or "—",
            _safe_date_label(row.get("date_of_joining")),
            row.get("mobile_no") or "—",
            _money(row.get("base_salary"), currency),
        ]
        for row in hr.get("recent") or []
    ]

    attendance_button = ""
    if staffing.get("report_exists"):
        attendance_button = (
            '<div class="hcc-inline-actions">'
            '<button type="button" class="btn btn-default btn-sm" '
            'data-hcc-report="{0}">{1}</button>'
            '</div>'
        ).format(_e(ATTENDANCE_REPORT), _e(_("Open attendance report")))

    body = _metric_grid(metrics) + attendance_button
    chart_specs = []
    if hr.get("available"):
        chart_specs.extend(
            [
                ("hr_department", "Employees and salary by department", "Headcount with total assigned base salary"),
                ("hr_status", "Employee status distribution", "Active, inactive, suspended and left"),
                ("hr_employment_type", "Employment types", "Employees by employment type"),
                ("hr_gender", "Gender distribution", "Employee master data"),
            ]
        )
    if staffing.get("available"):
        chart_specs.extend(
            [
                ("staff_absence_department", "Attendance by department", "Present, absent and not checked in"),
                ("staff_absence_mix", "Attendance exception mix", "Doctors, nurses and other staff"),
            ]
        )
    body += _chart_grid(chart_specs)

    if staffing.get("available"):
        body += '<div class="hcc-grid hcc-grid-2">'
        body += _table_card(
            "Attendance by department · {0}".format(staffing.get("attendance_date") or "—"),
            ["Department", "Staff", "Present", "Absent", "Not Checked In"],
            attendance_department_rows,
            nested=True,
        )
        body += _table_card(
            "Attendance exceptions from Employee Checkin",
            ["Employee", "Employee Name", "Department", "Designation",  "Status", "First Checkin", "Last Checkin"],
            attendance_exception_rows,
            nested=True,
        )
        body += "</div>"

    if hr.get("available"):
        body += '<div class="hcc-grid hcc-grid-2">'
        body += _table_card(
            "Department summary",
            ["Department", "Employees", "Active", "Other Status", "Salary Cost"],
            department_rows,
            nested=True,
        )
        body += _table_card(
            "Recent employee joiners",
            [
                "Employee ID",
                "Employee Name",
                "Department",
                "Designation",
                "Employment Type",
                "Status",
                "Date of Joining",
                "Mobile",
                "Base Salary",
            ],
            recent_rows,
            nested=True,
        )
        body += "</div>"

    return _section_shell(
        "hr",
        "HUMAN RESOURCES + ATTENDANCE",
        "HR Section",
        "Employee master and salary data merged with daily Employee Checkin attendance. Any accepted punch is Present; Half Day is not used.",
        HR_REPORT if hr.get("report_exists") else None,
        body,
    )


def _render_inventory_section(inventory):
    if not inventory.get("available"):
        return _render_unavailable_section("inventory", "Inventory", inventory)

    t = inventory["totals"]
    currency = inventory.get("currency")
    metrics = [
        (_("Distinct Items"), _number(t["items"]), "BLUE"),
        (_("Warehouses"), _number(t["warehouses"]), "BLUE"),
        (_("Current Quantity"), _number(t["quantity"], 2), "BLUE"),
        (_("Stock Value"), _money(t["stock_value"], currency), "GREEN"),
        (_("Average Cost"), _money(t["average_cost"], currency), "BLUE"),
        (_("Sales Value"), _money(t["sales_value"], currency), "BLUE"),
        (_("Negative Stock Lines"), _number(t["negative_stock"]), "AMBER" if t["negative_stock"] else "GREEN"),
        (_("Expired Item Lines"), _number(t["expired_items"]), "AMBER" if t["expired_items"] else "GREEN"),
    ]

    warehouse_rows = [
        [
            row.get("warehouse"),
            _number(row.get("items")),
            _number(row.get("quantity"), 2),
            _money(row.get("stock_value"), currency),
            _number(row.get("negative_stock")),
            _number(row.get("expired_items")),
        ]
        for row in inventory.get("by_warehouse") or []
    ]

    item_rows = [
        [
            row.get("warehouse"),
            row.get("item_code"),
            row.get("item_name") or row.get("item_code"),
            row.get("item_group") or "—",
            row.get("sub_category") or "—",
            _number(row.get("actual_qty"), 2),
            _money(row.get("stock_value"), currency),
            _money(row.get("avg_cost"), currency),
            _money(row.get("standard_rate"), currency),
            _safe_date_label(row.get("expired_date")),
        ]
        for row in inventory.get("top_items") or []
    ]

    expired_rows = [
        [
            row.get("warehouse"),
            row.get("item_code"),
            row.get("item_name") or row.get("item_code"),
            _number(row.get("actual_qty"), 2),
            _safe_date_label(row.get("expired_date")),
            _money(row.get("stock_value"), currency),
        ]
        for row in inventory.get("expired") or []
    ]

    return _section_shell(
        "inventory",
        "INVENTORY",
        "Inventory Section",
        "Current stock is grouped by Warehouse from Bin, Item and Warehouse. A selected group warehouse expands to its leaf warehouses.",
        INVENTORY_REPORT if inventory.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("inventory_warehouse_value", "Stock value by warehouse", "Warehouse-grouped Bin value"),
                ("inventory_warehouse_qty", "Quantity by warehouse", "Current quantity by warehouse"),
                ("inventory_item_group", "Stock value by item group", "Top item groups by value"),
                ("inventory_stock_health", "Stock-line health", "Positive, zero and negative warehouse/item lines"),
            ]
        )
        + _table_card(
            "Warehouse stock summary",
            ["Warehouse", "Items", "Quantity", "Stock Value", "Negative Lines", "Expired Lines"],
            warehouse_rows,
        )
        + _table_card(
            "Top inventory items by stock value",
            [
                "Warehouse",
                "Item",
                "Item Name",
                "Item Group",
                "Sub Category",
                "Current Qty",
                "Stock Value",
                "Avg Cost",
                "Sales Price",
                "Expired Date",
            ],
            item_rows,
        )
        + _table_card(
            "Expired item lines",
            ["Warehouse", "Item", "Item Name", "Quantity", "Expired Date", "Stock Value"],
            expired_rows,
        ),
    )


def _render_sales_section(sales):
    if not sales.get("available"):
        return _render_unavailable_section("sales", "Revenue", sales)

    t = sales["totals"]
    currency = sales.get("currency")
    metrics = [
        (_("Invoices"), _number(t["invoices"]), "BLUE"),
        (_("Revenue"), _money(t["revenue"], currency), "GREEN"),
        (_("Collected"), _money(t["collected"], currency), "GREEN"),
        (_("Outstanding"), _money(t["outstanding"], currency), "AMBER" if t["outstanding"] else "GREEN"),
        (_("Payments Received"), _money(t["payments_received"], currency), "BLUE"),
        (_("Average Invoice"), _money(t["average_invoice"], currency), "BLUE"),
        (_("Paid Invoices"), _number(t["paid_invoices"]), "GREEN"),
        (_("Overdue Invoices"), _number(t["overdue_invoices"]), "RED" if t["overdue_invoices"] else "GREEN"),
    ]

    customer_rows = [
        [
            row.get("customer_name") or row.get("customer"),
            _money(row.get("revenue"), currency),
            _money(row.get("collected"), currency),
            _money(row.get("outstanding"), currency),
            _number(row.get("invoices")),
        ]
        for row in sales.get("top_customers") or []
    ]

    item_rows = [
        [
            row.get("item_group"),
            _money(row.get("amount"), currency),
            _number(row.get("quantity"), 2),
            _number(row.get("invoice_count")),
        ]
        for row in sales.get("item_groups") or []
    ]

    return _section_shell(
        "sales",
        "SALES DASHBOARD",
        "Sales Dashboard",
        "Submitted Sales Invoices use base-currency totals when available; payments come from submitted Receive Payment Entries.",
        SALES_REPORT if sales.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("sales_trend", "Revenue and outstanding trend", "Invoice posting date"),
                ("sales_status", "Invoice status distribution", "Submitted invoices"),
                ("sales_customers", "Top customers", "Revenue by customer"),
                ("sales_items", "Top item groups", "Net amount by item group"),
            ]
        )
        + '<div class="hcc-grid hcc-grid-2">'
        + _table_card(
            "Top customers",
            ["Customer Name", "Revenue", "Collected", "Outstanding", "Invoices"],
            customer_rows,
            nested=True,
        )
        + _table_card(
            "Top item groups",
            ["Item Group", "Net Amount", "Quantity", "Invoices"],
            item_rows,
            nested=True,
        )
        + "</div>",
    )


def _render_finance_section(finance):
    if not finance.get("available"):
        return _render_unavailable_section("finance", "Finance", finance)

    t = finance["totals"]
    currency = finance.get("currency")
    metrics = [
        (_("Income"), _money(t["income"], currency), "GREEN"),
        (_("Expense"), _money(t["expense"], currency), "BLUE"),
        (_("Net Profit"), _money(t["net_profit"], currency), "RED" if t["net_profit"] < 0 else "GREEN"),
        (_("Receivables"), _money(t["receivable"], currency), "AMBER" if t["receivable"] else "GREEN"),
        (_("Payables"), _money(t["payable"], currency), "BLUE"),
    ]

    ar_rows = [
        [index + 1, row.get("customer_name") or row.get("party"), _money(row.get("balance"), currency)]
        for index, row in enumerate(finance.get("top_receivables") or [])
    ]
    ap_rows = [
        [index + 1, row.get("party"), _money(row.get("balance"), currency)]
        for index, row in enumerate(finance.get("top_payables") or [])
    ]

    return _section_shell(
        "finance",
        "FINANCIAL DASHBOARD",
        "Financial Dashboard",
        "P&L is calculated for the selected period. Receivables and payables are balances through To Date.",
        FINANCIAL_REPORT if finance.get("report_exists") else None,
        _metric_grid(metrics)
        + _chart_grid(
            [
                ("finance_pnl", "Monthly P&L", "Income, expense and net profit"),
                ("finance_balance", "AR and AP balances", "As of To Date"),
            ]
        )
        + '<div class="hcc-grid hcc-grid-2">'
        + _table_card("Top receivables", ["#", "Customer Name", "Balance"], ar_rows, nested=True)
        + _table_card("Top payables", ["#", "Supplier", "Balance"], ap_rows, nested=True)
        + "</div>",
    )


def _render_unavailable_section(tab, title, source):
    return _section_shell(
        tab,
        "SOURCE UNAVAILABLE",
        title,
        source.get("reason") or _("The source could not be loaded."),
        None,
        """
        <div class="hcc-empty">
            <strong>{title} data is unavailable</strong>
            <span>{reason}</span>
        </div>
        """.format(title=_e(title), reason=_e(source.get("reason"))),
    )


def _section_shell(tab, kicker, title, description, report, body):
    report_button = ""
    if report:
        report_button = (
            '<button type="button" class="btn btn-default btn-sm" '
            'data-hcc-report="{0}">Open detailed report</button>'
        ).format(_e(report))

    section_icons = {
        "overview": "🏥",
        "opd": "👥",
        "queue": "⏱️",
        "ipd": "🛏️",
        "ot": "🩺",
        "lab": "🧪",
        "pharmacy": "💊",
        "warehouse": "🏬",
        "optical": "👓",
        "staffing": "🧑‍⚕️",
        "hr": "🧑‍⚕️",
        "inventory": "📦",
        "sales": "💳",
        "finance": "📈",
    }
    icon = section_icons.get(tab, "📊")

    return """
    <section id="hcc-section-{tab}" class="hcc-section hcc-section-{tab}" data-hcc-section="{tab}">
        <div class="hcc-section-heading">
            <div class="hcc-section-title-row">
                <div class="hcc-section-icon" aria-hidden="true">{icon}</div>
                <div>
                    <span class="hcc-section-kicker">{kicker}</span>
                    <h2>{title}</h2>
                    <p>{description}</p>
                </div>
            </div>
            {report_button}
        </div>
        {body}
    </section>
    """.format(
        tab=_e(tab),
        icon=_e(icon),
        kicker=_e(kicker),
        title=_e(title),
        description=_e(description),
        report_button=report_button,
        body=body,
    )


def _metric_icon(label):
    text = str(label or "").lower()
    icon_rules = (
        (("new patient",), "🆕"),
        (("follow", "revisit"), "🔁"),
        (("refer",), "📤"),
        (("queue wait", "longest queue", "average queue", "over 20"), "⏱️"),
        (("pending", "outstanding", "receivable", "waiting"), "⏳"),
        (("completed", "closed", "paid invoices", "discharged"), "✅"),
        (("discharge scheduled",), "📋"),
        (("cancelled", "overdue", "abnormal"), "⚠️"),
        (("doctor", "admitted", "admission", "ot cases", "procedure"), "🩺"),
        (("pre-op", "consent", "iol"), "📋"),
        (("test", "lab result", "evaluation"), "🧪"),
        (("expired", "negative stock", "stockout", "critical drugs"), "⚠️"),
        (("pharmacy", "prescription", "dispensed"), "💊"),
        (("glasses", "frames", "optical", "eye patients"), "👓"),
        (("present staff", "absent staff", "absent doctors", "absent nurses", "attendance rate", "not checked in"), "🧑‍⚕️"),
        (("warehouse", "inventory", "stock", "item group", "distinct items"), "📦"),
        (("employee", "employment", "department"), "👤"),
        (("salary",), "💼"),
        (("normal",), "🟢"),
        (("revenue", "income", "collected", "payments received"), "💰"),
        (("average",), "📊"),
        (("expense", "payables"), "💸"),
        (("profit",), "📈"),
        (("invoice",), "🧾"),
        (("total opd", "total ipd", "patients"), "👥"),
    )
    for needles, icon in icon_rules:
        if any(needle in text for needle in needles):
            return icon
    return "📌"


def _metric_grid(metrics):
    cards = []
    for label, value, status in metrics:
        status = status or "GREY"
        cards.append(
            """
            <article class="hcc-metric hcc-status-{status}">
                <div class="hcc-metric-top">
                    <span class="hcc-metric-icon" aria-hidden="true">{icon}</span>
                    <span class="hcc-metric-label">{label}</span>
                </div>
                <strong>{value}</strong>
                <span class="hcc-metric-state">{state}</span>
            </article>
            """.format(
                status=_e(str(status).lower()),
                icon=_e(_metric_icon(label)),
                label=_e(label),
                value=_e(value),
                state=_e(str(status).upper()),
            )
        )
    return '<div class="hcc-metric-grid">{0}</div>'.format("".join(cards))

def _chart_grid(charts):
    html = []
    for key, title, subtitle in charts:
        html.append(
            """
            <article class="hcc-panel hcc-chart-card">
                <div class="hcc-card-heading">
                    <div><h3>{title}</h3><p>{subtitle}</p></div>
                </div>
                <div class="hcc-chart" data-chart-key="{key}"></div>
            </article>
            """.format(title=_e(title), subtitle=_e(subtitle), key=_e(key))
        )
    return '<div class="hcc-chart-grid">{0}</div>'.format("".join(html))


def _table_card(title, headers, rows, nested=False):
    header_html = "".join("<th>{0}</th>".format(_e(header)) for header in headers)
    if rows:
        body_html = "".join(
            "<tr>{0}</tr>".format(
                "".join("<td>{0}</td>".format(_e(value)) for value in row)
            )
            for row in rows
        )
    else:
        body_html = '<tr><td colspan="{0}" class="hcc-table-empty">No data for the selected filters.</td></tr>'.format(
            len(headers)
        )

    card_class = "hcc-panel hcc-table-card"
    if nested:
        card_class += " hcc-nested"

    return """
    <article class="{card_class}">
        <div class="hcc-card-heading"><div><h3>{title}</h3></div></div>
        <div class="hcc-table-wrap">
            <table class="hcc-table">
                <thead><tr>{headers}</tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    </article>
    """.format(
        card_class=card_class,
        title=_e(title),
        headers=header_html,
        rows=body_html,
    )
