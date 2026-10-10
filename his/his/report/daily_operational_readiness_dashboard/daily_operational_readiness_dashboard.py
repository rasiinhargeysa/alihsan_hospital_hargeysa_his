# Copyright (c) 2026, Al-Ihsan Hospital and contributors
# For license information, please see license.txt

"""Daily Operational Readiness Dashboard.

Professional admin Script Report for the custom DocType:

    Daily Operational Readiness

Expected parent fields:
    user
    date
    checklist_type
    operational_readiness_item  (Table)

Expected child fields:
    question
    answer

The dashboard uses the same question catalogue as the supplied Client Script.
N/A answers are excluded from compliance. Missing or unanswered mandatory
questions never count as compliant.
"""

from __future__ import unicode_literals

import json
from collections import Counter, defaultdict
from datetime import timedelta
from html import escape
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate


REPORT_NAME = "Daily Operational Readiness Dashboard"
PARENT_DOCTYPE = "Daily Operational Readiness"
CHILD_TABLE_FIELD = "operational_readiness_item"

ALLOWED_ROLES = {
    "System Manager",
    "Healthcare Administrator",
}

# These questions mirror the supplied Client Script. Keeping the catalogue here
# lets the admin dashboard show checklist types that are completely missing and
# detect old/incomplete records whose child rows no longer match the approved set.
CHECKLIST_QUESTIONS = {
    "Hospital Readiness": [
        "Is reception ready to receive patients?",
        "Is the registration and HIS system working?",
        "Are all scheduled OPD rooms ready?",
        "Is the Emergency Department fully ready?",
        "Is the laboratory ready to provide services?",
        "Is the radiology department operational?",
        "Is the pharmacy ready to dispense medicines?",
        "Is the operating theatre ready for scheduled procedures?",
        "Is ICU ready to receive patients?",
        "Are all available ward beds prepared?",
        "Has morning cleaning been completed?",
        "Are security personnel at their assigned stations?",
        "Are electricity, water and oxygen available?",
        "Are HIS, queue management and billing systems working?",
    ],
    "Doctor and Nurse Availability": [
        "Is the scheduled ophthalmologist present and available?",
        "Is the scheduled ENT specialist present and available?",
        "Is the pediatrician present and available?",
        "Is the OBS & GYN specialist present and available?",
        "Are all required Emergency Department staff present?",
        "Are the required nurses available in each department?",
        "Are laboratory personnel available?",
        "Are radiology personnel available?",
        "Are pharmacy personnel available?",
        "Are customer care personnel available?",
        "Are staff absences reported and replacement arrangements made?",
        "Are all scheduled clinical services adequately staffed?",
    ],
    "Critical Service Availability": [
        "Is the Emergency Department operational?",
        "Is ICU operational?",
        "Is the operating theatre available?",
        "Are laboratory services available?",
        "Is CT scanning available?",
        "Is X-ray available?",
        "Is ultrasound available?",
        "Is the pharmacy operational?",
        "Is medical oxygen available?",
        "Is the ambulance/transport service available?",
    ],
    "Critical Equipment": [
        "Is the CT scanner functioning?",
        "Is the X-ray machine functioning?",
        "Is essential OT equipment functioning?",
        "Is the anesthesia machine operational and checked?",
        "Are required ventilators operational?",
        "Are patient monitors functioning?",
        "Are laboratory analyzers operational?",
        "Is essential ophthalmology equipment operational?",
        "Have identified equipment faults been reported?",
        "Has unsafe equipment been labelled and removed from use?",
        "Is backup equipment available where required?",
        "Have required daily equipment checks been documented?",
    ],
    "Medicine and Supply": [
        "Are all required essential medicines available?",
        "Are critical emergency medicines available?",
        "Are critical medical consumables available?",
        "Are required laboratory reagents available?",
        "Is medical oxygen stock sufficient?",
        "Are scheduled surgical consumables available?",
        "Have items below minimum stock been identified?",
        "Have expired items been identified and segregated?",
        "Have critical stock-outs been reported?",
        "Have urgent procurement requests been submitted?",
        "Are alternative arrangements available for critical shortages?",
    ],
    "Laboratory and Diagnostic": [
        "Is the laboratory operational?",
        "Are at least 95% of required laboratory equipment available?",
        "Is reagent availability at least 95%?",
        "Is the sample rejection rate below 2%?",
        "Is laboratory TAT compliance at least 95%?",
        "Have all critical results been communicated?",
        "Is CT availability at least 95%?",
        "Is X-ray availability at least 95%?",
        "Is diagnostic reporting TAT compliance at least 95%?",
        "Have all diagnostic service interruptions been reported?",
    ],
    "Operating Theatre and Surgery": [
        "Has today's surgical schedule been confirmed?",
        "Is the operating theatre ready?",
        "Are scheduled surgeons available?",
        "Is the anesthesia team available?",
        "Are surgical instruments ready and sterile?",
        "Are required surgical consumables available?",
        "Have all scheduled patients completed preoperative assessment?",
        "Are recovery beds available?",
        "Is emergency backup ready?",
        "Have surgical delays been documented?",
        "Have cancelled operations and reasons been recorded?",
        "Have completed operations been recorded?",
    ],
    "Facility and Environment": [
        "Is reception clean?",
        "Are OPD waiting areas clean?",
        "Are consultation rooms clean?",
        "Is the Emergency Department clean?",
        "Is the operating theatre clean?",
        "Is ICU clean?",
        "Are patient wards clean?",
        "Are toilets clean and functional?",
        "Is medical waste properly segregated and controlled?",
        "Is water available in required areas?",
        "Is air conditioning working?",
        "Is lighting functioning?",
        "Are patient and staff areas free from visible hazards?",
    ],
    "Utilities and Fire Safety": [
        "Is main electricity available?",
        "Is the backup generator operational?",
        "Is generator fuel sufficient?",
        "Is the main water supply available?",
        "Is backup water storage sufficient?",
        "Is the oxygen supply operational?",
        "Is backup oxygen available?",
        "Is the internet working?",
        "Is backup internet available?",
        "Is the fire alarm system operational?",
        "Are fire extinguishers accessible and inspection-valid?",
        "Are emergency exits clear and accessible?",
        "Is the CCTV/security system functioning?",
    ],
    "Patient Experience": [
        "Has patient satisfaction feedback been collected?",
        "Is patient satisfaction at least 90%?",
        "Have all complaints received today been registered?",
        "Have serious complaints been escalated?",
        "Is complaint resolution within 48 hours at least 95%?",
        "Have patients reporting long waiting times been identified?",
        "Have waiting-time problems been communicated to the relevant department?",
        "Have the top three complaint categories been recorded?",
        "Have unresolved complaints been assigned to responsible staff?",
        "Have patients received updates about unresolved complaints?",
    ],
    "Incident and Patient Safety": [
        "Have all known incidents been registered?",
        "Have Level 1 incidents been recorded?",
        "Have Level 2 incidents been recorded?",
        "Have Level 3 incidents been recorded?",
        "Have Level 4 incidents been immediately escalated?",
        "Have Level 5 critical incidents been immediately escalated?",
        "Have unresolved patient-safety issues been identified?",
        "Have repeated incidents been identified?",
        "Have incidents requiring senior management intervention been escalated?",
        "Have corrective actions been assigned?",
        "Have closed incidents been verified?",
    ],
    "Problems Requiring Action": [
        "Have all critical problems been identified?",
        "Have problems been assigned to the correct department?",
        "Has each problem been classified by risk?",
        "Has every problem been assigned an owner?",
        "Has a resolution deadline been assigned?",
        "Have immediate-risk problems been escalated?",
        "Have overdue problems been identified?",
        "Have responsible persons provided progress updates?",
        "Have completed corrective actions been verified?",
        "Have unresolved problems been carried forward?",
        "Have recurring problems been identified?",
        "Has the Operations Manager reviewed today's action list?",
    ],
}

CHECKLIST_TYPES = tuple(CHECKLIST_QUESTIONS.keys())
CHECKLIST_ORDER = {name: index for index, name in enumerate(CHECKLIST_TYPES)}

# The current DocType has no per-question critical flag. Until one is added,
# these categories are treated as safety/continuity-critical. The configuration
# is intentionally kept at the top of the report for easy review.
CRITICAL_CHECKLIST_TYPES = {
    "Hospital Readiness",
    "Doctor and Nurse Availability",
    "Critical Service Availability",
    "Critical Equipment",
    "Medicine and Supply",
    "Laboratory and Diagnostic",
    "Operating Theatre and Surgery",
    "Utilities and Fire Safety",
    "Incident and Patient Safety",
    "Problems Requiring Action",
}

STATUS_RANK = {
    "GREY": 0,
    "BLUE": 1,
    "GREEN": 2,
    "AMBER": 3,
    "RED": 4,
}

STATUS_COLORS = {
    "BLUE": "#2563eb",
    "GREEN": "#15803d",
    "AMBER": "#d97706",
    "RED": "#b91c1c",
    "GREY": "#64748b",
}

STATUS_BACKGROUNDS = {
    "BLUE": "#eff6ff",
    "GREEN": "#ecfdf3",
    "AMBER": "#fff7ed",
    "RED": "#fef2f2",
    "GREY": "#f1f5f9",
}

CHECKLIST_CARD_ICONS = {
    "Hospital Readiness": "✚",
    "Doctor and Nurse Availability": "●",
    "Critical Service Availability": "✦",
    "Critical Equipment": "⚙",
    "Medicine and Supply": "◆",
    "Laboratory and Diagnostic": "⚗",
    "Operating Theatre and Surgery": "✣",
    "Facility and Environment": "⌂",
    "Utilities and Fire Safety": "⚡",
    "Patient Experience": "♥",
    "Incident and Patient Safety": "⛨",
    "Problems Requiring Action": "!",
}

STATUS_DISPLAY_LABELS = {
    "GREEN": _("Ready"),
    "AMBER": _("Attention"),
    "RED": _("Action"),
    "GREY": _("Pending"),
    "BLUE": _("Monitoring"),
}


# ---------------------------------------------------------------------------
# Script Report entry point
# ---------------------------------------------------------------------------


def execute(filters=None):
    _assert_admin_access()

    filters = frappe._dict(filters or {})
    _set_filter_defaults(filters)
    _validate_filters(filters)

    schema = _get_schema()
    raw_rows = _get_raw_rows(filters, schema)
    records = _aggregate_documents(raw_rows)

    if filters.get("status"):
        records = [row for row in records if row.status == filters.status]

    columns = get_columns()
    data = [_export_row(row) for row in records]
    message = _render_dashboard(filters, records)

    # columns, data, message, chart, report_summary, skip_total_rows
    return columns, data, message, None, [], 1


# ---------------------------------------------------------------------------
# Access, schema and filters
# ---------------------------------------------------------------------------


def _assert_admin_access():
    if frappe.session.user == "Administrator":
        return

    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(ALLOWED_ROLES):
        frappe.throw(
            _("You are not permitted to open the Daily Operational Readiness Dashboard."),
            frappe.PermissionError,
        )


def _set_filter_defaults(filters):
    filters.date = filters.get("date") or nowdate()


def _validate_filters(filters):
    if not filters.get("date"):
        frappe.throw(_("Please select a Date."))

    try:
        selected_date = getdate(filters.date)
    except Exception:
        frappe.throw(_("Please select a valid Date."))

    # Keep the filter in a canonical form for SQL and export output.
    filters.date = selected_date.isoformat()

    if filters.get("checklist_type") and filters.checklist_type not in CHECKLIST_TYPES:
        frappe.throw(_("The selected Checklist Type is not part of the approved question catalogue."))


def _doctype_fields(doctype):
    if not frappe.db.exists("DocType", doctype):
        return set()

    meta = frappe.get_meta(doctype)
    standard = {
        "name",
        "creation",
        "modified",
        "owner",
        "modified_by",
        "docstatus",
        "idx",
    }
    return standard.union({field.fieldname for field in meta.fields if field.fieldname})


def _get_schema():
    if not frappe.db.exists("DocType", PARENT_DOCTYPE):
        frappe.throw(_("DocType {0} was not found.").format(PARENT_DOCTYPE))

    parent_meta = frappe.get_meta(PARENT_DOCTYPE)
    parent_fields = _doctype_fields(PARENT_DOCTYPE)
    required_parent = {"user", "date", "checklist_type", CHILD_TABLE_FIELD}
    missing_parent = sorted(required_parent - parent_fields)

    if missing_parent:
        frappe.throw(
            _("{0} is missing required fields: {1}").format(
                PARENT_DOCTYPE,
                ", ".join(missing_parent),
            )
        )

    table_field = parent_meta.get_field(CHILD_TABLE_FIELD)
    child_doctype = table_field.options if table_field else None

    if not child_doctype or not frappe.db.exists("DocType", child_doctype):
        frappe.throw(
            _("The child DocType configured on {0}.{1} was not found.").format(
                PARENT_DOCTYPE,
                CHILD_TABLE_FIELD,
            )
        )

    child_fields = _doctype_fields(child_doctype)
    missing_child = sorted({"question", "answer"} - child_fields)

    if missing_child:
        frappe.throw(
            _("{0} is missing required fields: {1}").format(
                child_doctype,
                ", ".join(missing_child),
            )
        )

    return frappe._dict(
        child_doctype=child_doctype,
        child_table=_sql_table(child_doctype),
    )


def _sql_table(doctype):
    return "`tab{0}`".format(str(doctype).replace("`", "``"))


# ---------------------------------------------------------------------------
# Data retrieval and aggregation
# ---------------------------------------------------------------------------


def _get_raw_rows(filters, schema):
    conditions = [
        "parent_doc.`date` = %(date)s",
        "parent_doc.docstatus < 2",
    ]

    values = {
        "date": filters.date,
    }

    if filters.get("checklist_type"):
        conditions.append("parent_doc.checklist_type = %(checklist_type)s")
        values["checklist_type"] = filters.checklist_type

    if filters.get("user"):
        conditions.append("parent_doc.`user` = %(user)s")
        values["user"] = filters.user

    return frappe.db.sql(
        """
        SELECT
            parent_doc.name,
            parent_doc.`date`,
            parent_doc.checklist_type,
            parent_doc.`user`,
            COALESCE(NULLIF(TRIM(user_doc.full_name), ''), parent_doc.`user`) AS full_name,
            parent_doc.owner,
            parent_doc.creation,
            parent_doc.modified,
            parent_doc.docstatus,
            child.idx AS question_no,
            child.question,
            child.answer

        FROM `tabDaily Operational Readiness` parent_doc

        LEFT JOIN {child_table} child
            ON child.parent = parent_doc.name
            AND child.parenttype = %(parenttype)s
            AND child.parentfield = %(parentfield)s

        LEFT JOIN `tabUser` user_doc
            ON user_doc.name = parent_doc.`user`

        WHERE {where_clause}

        ORDER BY
            parent_doc.`date` DESC,
            parent_doc.modified DESC,
            parent_doc.name DESC,
            child.idx ASC
        """.format(
            child_table=schema.child_table,
            where_clause=" AND ".join(conditions),
        ),
        dict(
            values,
            parenttype=PARENT_DOCTYPE,
            parentfield=CHILD_TABLE_FIELD,
        ),
        as_dict=True,
    )


def _aggregate_documents(raw_rows):
    documents = {}

    for raw in raw_rows or []:
        name = raw.name
        record = documents.get(name)

        if not record:
            record = frappe._dict(
                name=name,
                date=getdate(raw.date),
                checklist_type=(raw.checklist_type or "Unspecified").strip(),
                user=raw.user or "",
                full_name=raw.full_name or raw.user or "",
                owner=raw.owner or "",
                creation=raw.creation,
                modified=raw.modified,
                docstatus=cint(raw.docstatus),
                responses=[],
            )
            documents[name] = record

        if raw.question is not None or raw.answer is not None:
            record.responses.append(
                frappe._dict(
                    idx=cint(raw.question_no),
                    question=(raw.question or "").strip(),
                    answer=_normalise_answer(raw.answer),
                    raw_answer=(raw.answer or "").strip(),
                )
            )

    records = []

    for record in documents.values():
        expected_questions = CHECKLIST_QUESTIONS.get(record.checklist_type, [])
        expected_set = set(expected_questions)
        actual_questions = [response.question for response in record.responses if response.question]
        actual_set = set(actual_questions)

        counts = Counter(response.answer for response in record.responses)
        yes_count = counts.get("Yes", 0)
        no_count = counts.get("No", 0)
        na_count = counts.get("N/A", 0)
        blank_count = counts.get("Unanswered", 0)

        missing_questions = [question for question in expected_questions if question not in actual_set]
        unexpected_questions = [question for question in actual_questions if expected_set and question not in expected_set]

        # Missing approved questions are treated as unanswered. This prevents an
        # old or modified checklist from becoming green with fewer rows.
        unanswered_count = blank_count + len(missing_questions)
        applicable_count = yes_count + no_count
        compliance = (yes_count / applicable_count * 100) if applicable_count else None

        catalogue_known = bool(expected_questions)

        record.update(
            expected_questions=len(expected_questions),
            actual_questions=len(record.responses),
            yes_count=yes_count,
            no_count=no_count,
            na_count=na_count,
            unanswered_count=unanswered_count,
            applicable_count=applicable_count,
            compliance=compliance,
            missing_questions=missing_questions,
            unexpected_questions=unexpected_questions,
            question_set_valid=(
                catalogue_known
                and not missing_questions
                and not unexpected_questions
                and len(record.responses) == len(expected_questions)
            ),
        )

        record.complete = bool(
            record.question_set_valid
            and record.unanswered_count == 0
            and record.applicable_count > 0
        )
        record.status = _record_status(record)
        record.status_reason = _record_status_reason(record)
        records.append(record)

    records.sort(
        key=lambda row: (
            row.date,
            row.modified or row.creation,
            row.name,
        ),
        reverse=True,
    )
    return records


def _normalise_answer(value):
    text = str(value or "").strip().lower()

    if text == "yes":
        return "Yes"
    if text == "no":
        return "No"
    if text in {"n/a", "na", "not applicable"}:
        return "N/A"
    return "Unanswered"


def _record_status(record):
    if record.unanswered_count or not record.question_set_valid or not record.applicable_count:
        return "GREY"

    if record.no_count == 0:
        return "GREEN"

    if (
        record.checklist_type in CRITICAL_CHECKLIST_TYPES
        or flt(record.compliance) < 85
    ):
        return "RED"

    return "AMBER"


def _record_status_reason(record):
    if record.unanswered_count:
        return _("{0} unanswered or missing mandatory question(s)").format(record.unanswered_count)
    if not record.question_set_valid:
        return _("The saved question set does not match the approved checklist")
    if not record.applicable_count:
        return _("No applicable Yes/No responses")
    if record.no_count == 0:
        return _("All applicable requirements passed")
    if record.status == "RED":
        return _("{0} failed requirement(s) require immediate escalation").format(record.no_count)
    return _("{0} failed requirement(s) require management action").format(record.no_count)


# ---------------------------------------------------------------------------
# Report table
# ---------------------------------------------------------------------------


def get_columns():
    return [
        {
            "label": _("Date"),
            "fieldname": "date",
            "fieldtype": "Date",
            "width": 105,
        },
        {
            "label": _("Checklist"),
            "fieldname": "name",
            "fieldtype": "Link",
            "options": PARENT_DOCTYPE,
            "width": 185,
        },
        {
            "label": _("Checklist Type"),
            "fieldname": "checklist_type",
            "fieldtype": "Data",
            "width": 230,
        },
        {
            "label": _("User"),
            "fieldname": "user",
            "fieldtype": "Link",
            "options": "User",
            "width": 180,
        },
        {
            "label": _("Full Name"),
            "fieldname": "full_name",
            "fieldtype": "Data",
            "width": 190,
        },
        {
            "label": _("Yes"),
            "fieldname": "yes_count",
            "fieldtype": "Int",
            "width": 70,
        },
        {
            "label": _("No"),
            "fieldname": "no_count",
            "fieldtype": "Int",
            "width": 70,
        },
        {
            "label": _("N/A"),
            "fieldname": "na_count",
            "fieldtype": "Int",
            "width": 70,
        },
        {
            "label": _("Unanswered"),
            "fieldname": "unanswered_count",
            "fieldtype": "Int",
            "width": 105,
        },
        {
            "label": _("Compliance"),
            "fieldname": "compliance",
            "fieldtype": "Percent",
            "width": 105,
            "precision": 1,
        },
        {
            "label": _("Status"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 95,
        },
        {
            "label": _("Modified"),
            "fieldname": "modified",
            "fieldtype": "Datetime",
            "width": 150,
        },
    ]


def _export_row(record):
    return {
        "date": record.date,
        "name": record.name,
        "checklist_type": record.checklist_type,
        "user": record.user,
        "full_name": record.full_name,
        "yes_count": record.yes_count,
        "no_count": record.no_count,
        "na_count": record.na_count,
        "unanswered_count": record.unanswered_count,
        "compliance": round(flt(record.compliance), 1) if record.compliance is not None else None,
        "status": record.status,
        "modified": record.modified,
    }


# ---------------------------------------------------------------------------
# Dashboard calculations
# ---------------------------------------------------------------------------


def _render_dashboard(filters, records):
    if filters.get("checklist_type"):
        expected_types = [filters.checklist_type]
    elif filters.get("status"):
        expected_types = sorted(
            {row.checklist_type for row in records},
            key=lambda value: CHECKLIST_ORDER.get(value, 999),
        )
    else:
        expected_types = list(CHECKLIST_TYPES)

    focus_date = getdate(filters.date)
    focus_records = [row for row in records if row.date == focus_date]

    type_summaries = _build_type_summaries(focus_records, expected_types)
    overall = _build_overall(focus_records, type_summaries, expected_types)
    selected_date_totals = _sum_records(focus_records)
    focus_totals = selected_date_totals
    trend = _build_daily_trend(records, expected_types)
    actions = _build_actions(focus_records)
    hotspots = _build_failure_hotspots(records)
    duplicates = _find_duplicates(records)

    notices = []
    if overall.missing_type_names:
        notices.append(
            _notice(
                "AMBER",
                _("Missing checklist types"),
                ", ".join(overall.missing_type_names),
            )
        )
    if duplicates:
        notices.append(
            _notice(
                "AMBER",
                _("Duplicate checklist records detected"),
                _("{0} duplicate date/type/user combination(s) are included in the totals.").format(
                    len(duplicates)
                ),
            )
        )
    invalid_sets = [row for row in focus_records if not row.question_set_valid]
    if invalid_sets:
        notices.append(
            _notice(
                "RED",
                _("Question catalogue mismatch"),
                _("{0} checklist record(s) do not match the approved Client Script question set.").format(
                    len(invalid_sets)
                ),
            )
        )

    charts = {
        "compliance_by_type": {
            "type": "bar",
            "height": 300,
            "colors": ["#1686e5"],
            "data": {
                "labels": [summary.short_label for summary in type_summaries],
                "datasets": [
                    {
                        "name": _("Compliance %"),
                        "values": [round(flt(summary.compliance), 1) for summary in type_summaries],
                    }
                ],
            },
        },
        "response_mix": {
            "type": "donut",
            "height": 280,
            "colors": ["#12b76a", "#f04438", "#98a2b3", "#f79009"],
            "data": {
                "labels": [_('Yes'), _('No'), _('N/A'), _('Unanswered')],
                "datasets": [
                    {
                        "name": _("Responses"),
                        "values": [
                            focus_totals.yes_count,
                            focus_totals.no_count,
                            focus_totals.na_count,
                            focus_totals.unanswered_count,
                        ],
                    }
                ],
            },
        },
        "daily_trend": {
            "type": "bar",
            "height": 300,
            "colors": ["#1686e5", "#12b76a"],
            "data": {
                "labels": [row.label for row in trend],
                "datasets": [
                    {
                        "name": _("Compliance %"),
                        "values": [round(flt(row.compliance), 1) for row in trend],
                    },
                    {
                        "name": _("Coverage %"),
                        "values": [round(flt(row.coverage), 1) for row in trend],
                    },
                ],
            },
        },
        "status_distribution": {
            "type": "donut",
            "height": 280,
            "colors": ["#12b76a", "#f79009", "#f04438", "#98a2b3"],
            "data": {
                "labels": ["GREEN", "AMBER", "RED", "GREY"],
                "datasets": [
                    {
                        "name": _("Checklist Types"),
                        "values": [
                            sum(1 for row in type_summaries if row.status == "GREEN"),
                            sum(1 for row in type_summaries if row.status == "AMBER"),
                            sum(1 for row in type_summaries if row.status == "RED"),
                            sum(1 for row in type_summaries if row.status == "GREY"),
                        ],
                    }
                ],
            },
        },
    }

    selected_date_label = _date_label(filters.date)
    selected_date_long = _date_long_label(filters.date)

    return """
    <style>{css}</style>
    <div id="dor-dashboard" class="dor-root">
        <script id="dor-dashboard-payload" type="application/json">{payload}</script>

        <header class="dor-hero">
            <div class="dor-hospital-mark" aria-hidden="true">
                <svg viewBox="0 0 72 72" role="img">
                    <path d="M19 61V22h34v39M8 61V34h11M64 61V34H53" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M30 14h12v8H30z" fill="none" stroke="currentColor" stroke-width="4"/>
                    <path d="M36 10v16M28 18h16" stroke="#f04438" stroke-width="4" stroke-linecap="round"/>
                    <path d="M27 33h4M41 33h4M27 43h4M41 43h4M33 61V51h6v10" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
                </svg>
            </div>
            <div class="dor-hero-copy">
                <span class="dor-eyebrow">AL-IHSAN HOSPITAL · ADMIN CONTROL CENTRE</span>
                <h1>Operations Readiness Dashboard</h1>
                <p>Daily status across the hospital’s approved operational readiness control areas.</p>
            </div>
            <div class="dor-hero-date">
                <span>Selected Date</span>
                <strong>{selected_date_long}</strong>
                <small>{record_count} checklist record(s)</small>
            </div>
        </header>

        {notices}

        <section class="dor-section dor-checklist-section">
            <div class="dor-section-heading">
                <div>
                    <span class="dor-kicker">CHECKLIST CONTROL</span>
                    <h2>Status by checklist type</h2>
                </div>
                <span class="dor-note">Select a card to focus or clear that checklist filter</span>
            </div>
            <div class="dor-type-grid">{type_cards}</div>
        </section>

        <section class="dor-section dor-overall-section">
            <div class="dor-section-heading">
                <div>
                    <span class="dor-kicker">EXECUTIVE SUMMARY</span>
                    <h2>Overall Status</h2>
                </div>
                <span class="dor-note">One consolidated control signal for {focus_date}</span>
            </div>
            {overall_panel}
        </section>

        <section class="dor-section dor-action-section">
            <div class="dor-section-heading">
                <div>
                    <span class="dor-kicker">MANAGEMENT ACTION</span>
                    <h2>Action Required Now</h2>
                </div>
                <span class="dor-note">All “No” answers on {focus_date}</span>
            </div>
            {actions_table}
        </section>

        <section class="dor-section">
            <div class="dor-section-heading">
                <div>
                    <span class="dor-kicker">VISUAL CONTROL</span>
                    <h2>Compliance, coverage and response mix</h2>
                </div>
            </div>
            <div class="dor-chart-grid">
                {chart_compliance}
                {chart_response}
                {chart_trend}
                {chart_status}
            </div>
        </section>

        <section class="dor-section">
            <div class="dor-section-heading">
                <div>
                    <span class="dor-kicker">PERFORMANCE DETAIL</span>
                    <h2>Checklist performance by type</h2>
                </div>
            </div>
            {type_table}
        </section>

        <section class="dor-two-column">
            <div class="dor-section">
                <div class="dor-section-heading">
                    <div>
                        <span class="dor-kicker">FAILURE ANALYSIS</span>
                        <h2>Failure concentration</h2>
                    </div>
                </div>
                {hotspot_table}
            </div>

            <div class="dor-section">
                <div class="dor-section-heading">
                    <div>
                        <span class="dor-kicker">RECENT ACTIVITY</span>
                        <h2>Latest checklist records</h2>
                    </div>
                </div>
                {latest_table}
            </div>
        </section>

        <footer class="dor-footer">
            Compliance = Yes ÷ (Yes + No). N/A is excluded. Missing or unanswered mandatory questions never count as compliant.
            Selected date totals: {date_yes} Yes · {date_no} No · {date_na} N/A · {date_unanswered} unanswered.
        </footer>
    </div>
    """.format(
        css=_dashboard_css(),
        payload=_json_for_html({"charts": charts}),
        selected_date_long=_e(selected_date_long),
        focus_date=_e(selected_date_label),
        record_count=_number(len(records)),
        notices="".join(notices),
        type_cards="".join(_type_card(summary) for summary in type_summaries),
        overall_panel=_overall_status_panel(overall, focus_totals, expected_types),
        actions_table=_actions_table(actions),
        chart_compliance=_chart_card("Compliance by Checklist Type", "compliance_by_type"),
        chart_response=_chart_card("Response Distribution", "response_mix"),
        chart_trend=_chart_card("Selected Date Readiness", "daily_trend"),
        chart_status=_chart_card("Checklist Status Distribution", "status_distribution"),
        type_table=_type_summary_table(type_summaries),
        hotspot_table=_hotspot_table(hotspots),
        latest_table=_latest_table(records[:12]),
        date_yes=_number(selected_date_totals.yes_count),
        date_no=_number(selected_date_totals.no_count),
        date_na=_number(selected_date_totals.na_count),
        date_unanswered=_number(selected_date_totals.unanswered_count),
    )


def _build_type_summaries(records, expected_types):
    grouped = defaultdict(list)
    for record in records:
        grouped[record.checklist_type].append(record)

    summaries = []

    for checklist_type in expected_types:
        rows = grouped.get(checklist_type, [])
        totals = _sum_records(rows)
        statuses = [row.status for row in rows]

        if not rows:
            status = "GREY"
            reason = _("Not recorded")
        else:
            status = max(statuses, key=lambda value: STATUS_RANK.get(value, 0))
            reason = _aggregate_reason(rows, status)

        summaries.append(
            frappe._dict(
                checklist_type=checklist_type,
                short_label=_short_checklist_label(checklist_type),
                documents=len(rows),
                contributors=len({row.user for row in rows if row.user}),
                yes_count=totals.yes_count,
                no_count=totals.no_count,
                na_count=totals.na_count,
                unanswered_count=totals.unanswered_count,
                applicable_count=totals.applicable_count,
                compliance=totals.compliance,
                status=status,
                reason=reason,
                last_modified=max((row.modified for row in rows), default=None),
            )
        )

    return summaries


def _sum_records(records):
    yes_count = sum(cint(row.yes_count) for row in records)
    no_count = sum(cint(row.no_count) for row in records)
    na_count = sum(cint(row.na_count) for row in records)
    unanswered_count = sum(cint(row.unanswered_count) for row in records)
    applicable_count = yes_count + no_count
    compliance = (yes_count / applicable_count * 100) if applicable_count else None

    return frappe._dict(
        yes_count=yes_count,
        no_count=no_count,
        na_count=na_count,
        unanswered_count=unanswered_count,
        applicable_count=applicable_count,
        compliance=compliance,
    )


def _build_overall(focus_records, type_summaries, expected_types):
    present_types = {row.checklist_type for row in focus_records}
    missing_type_names = [name for name in expected_types if name not in present_types]
    critical_failures = sum(
        row.no_count
        for row in focus_records
        if row.checklist_type in CRITICAL_CHECKLIST_TYPES
    )

    if not focus_records:
        status = "GREY"
        reason = _("No readiness checklist has been recorded for the selected daily focus date.")
    elif any(row.status == "RED" for row in type_summaries):
        status = "RED"
        reason = _("Immediate action is required for one or more critical readiness failures.")
    elif missing_type_names or any(row.status in {"AMBER", "GREY"} for row in type_summaries):
        status = "AMBER"
        if missing_type_names:
            reason = _("Management follow-up is required because checklist coverage is incomplete.")
        else:
            reason = _("One or more noncritical failures require management intervention.")
    else:
        status = "GREEN"
        reason = _("All recorded and required checklist types passed their applicable requirements.")

    return frappe._dict(
        status=status,
        reason=reason,
        types_present=len(present_types.intersection(set(expected_types))),
        missing_types=len(missing_type_names),
        missing_type_names=missing_type_names,
        critical_failures=critical_failures,
    )


def _build_daily_trend(records, expected_types):
    grouped = defaultdict(list)
    for row in records:
        grouped[row.date].append(row)

    trend = []
    for date_value in sorted(grouped):
        day_rows = grouped[date_value]
        totals = _sum_records(day_rows)
        types_present = len({row.checklist_type for row in day_rows}.intersection(set(expected_types)))
        coverage = (types_present / len(expected_types) * 100) if expected_types else 0
        trend.append(
            frappe._dict(
                date=date_value,
                label=date_value.strftime("%d %b"),
                compliance=totals.compliance or 0,
                coverage=coverage,
            )
        )
    return trend


def _build_actions(focus_records):
    actions = []

    for record in focus_records:
        for response in record.responses:
            if response.answer != "No":
                continue

            critical = record.checklist_type in CRITICAL_CHECKLIST_TYPES
            actions.append(
                frappe._dict(
                    risk="Critical" if critical else "Management",
                    status="RED" if critical else "AMBER",
                    checklist_type=record.checklist_type,
                    question=response.question or _("Question not available"),
                    user=record.full_name or record.user,
                    document=record.name,
                    date=record.date,
                )
            )

    actions.sort(
        key=lambda row: (
            0 if row.status == "RED" else 1,
            CHECKLIST_ORDER.get(row.checklist_type, 999),
            row.question,
        )
    )
    return actions[:100]


def _build_failure_hotspots(records):
    stats = {}

    for record in records:
        for response in record.responses:
            if response.answer != "No":
                continue

            key = (record.checklist_type, response.question)
            bucket = stats.setdefault(
                key,
                {
                    "count": 0,
                    "latest_date": record.date,
                    "status": "RED" if record.checklist_type in CRITICAL_CHECKLIST_TYPES else "AMBER",
                },
            )
            bucket["count"] += 1
            if record.date > bucket["latest_date"]:
                bucket["latest_date"] = record.date

    rows = [
        frappe._dict(
            checklist_type=key[0],
            question=key[1],
            count=value["count"],
            latest_date=value["latest_date"],
            status=value["status"],
        )
        for key, value in stats.items()
    ]
    rows.sort(
        key=lambda row: (
            -row.count,
            -row.latest_date.toordinal(),
            row.question,
        )
    )
    return rows[:10]


def _find_duplicates(records):
    counts = Counter((row.date, row.checklist_type, row.user) for row in records)
    return [key for key, count in counts.items() if count > 1]


def _aggregate_reason(rows, status):
    if status == "RED":
        return _("Critical failure or compliance below the minimum control threshold")
    if status == "AMBER":
        return _("Management intervention required")
    if status == "GREY":
        return _("Incomplete, missing or not applicable")
    return _("All applicable requirements passed")


def _compliance_status(compliance, applicable_count):
    if not applicable_count or compliance is None:
        return "GREY"
    if compliance >= 100:
        return "GREEN"
    if compliance >= 85:
        return "AMBER"
    return "RED"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _overall_status_panel(overall, totals, expected_types):
    expected_count = len(expected_types)
    coverage_ratio = (overall.types_present / expected_count) if expected_count else 0

    if totals.compliance is None or not totals.applicable_count:
        readiness_score = None
        progress_width = 0
    else:
        readiness_score = max(0, min(100, flt(totals.compliance) * coverage_ratio))
        progress_width = readiness_score

    status = overall.status if overall.status in STATUS_DISPLAY_LABELS else "GREY"
    display_label = STATUS_DISPLAY_LABELS.get(status, _("Pending"))
    score_label = _percent(readiness_score)
    total_gaps = cint(totals.no_count) + cint(totals.unanswered_count)

    return """
    <article class="dor-overall-panel dor-overall-panel-{status_class}">
        <div class="dor-overall-copy">
            <span class="dor-overall-kicker">Overall daily status</span>
            <div class="dor-overall-title-row">
                <strong>{status_label}</strong>
                <span>{display_label}</span>
            </div>
            <p>{reason}</p>
        </div>

        <div class="dor-overall-progress-area">
            <div class="dor-overall-progress-labels">
                <span>Overall readiness</span>
                <strong>{score}</strong>
            </div>
            <div class="dor-overall-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress_value}">
                <span style="width:{progress_width}%;"></span>
            </div>
            <div class="dor-overall-meta">
                <span><b>{critical}</b> critical gap(s)</span>
                <span><b>{missing}</b> missing checklist type(s)</span>
                <span><b>{unanswered}</b> unanswered</span>
                <span><b>{present}/{expected}</b> checklist coverage</span>
            </div>
        </div>

        <div class="dor-overall-score">
            <strong>{score}</strong>
            <span>{display_label}</span>
            <small>{gaps} total control gap(s)</small>
        </div>
    </article>
    """.format(
        status_class=status.lower(),
        status_label=_e(status),
        display_label=_e(display_label),
        reason=_e(overall.reason),
        score=_e(score_label),
        progress_value=int(round(progress_width)),
        progress_width="{0:.1f}".format(progress_width),
        critical=_number(overall.critical_failures),
        missing=_number(overall.missing_types),
        unanswered=_number(totals.unanswered_count),
        present=_number(overall.types_present),
        expected=_number(expected_count),
        gaps=_number(total_gaps),
    )


def _card(label, value, status, note, icon):
    status = status if status in STATUS_COLORS else "GREY"
    return """
    <article class="dor-card dor-card-{status}">
        <div class="dor-card-top">
            <span class="dor-card-icon dor-icon-{icon}" aria-hidden="true"></span>
            <span>{label}</span>
        </div>
        <strong>{value}</strong>
        <small>{note}</small>
    </article>
    """.format(
        status=status.lower(),
        icon=_e(icon),
        label=_e(label),
        value=_e(value),
        note=_e(note),
    )


def _notice(status, title, text):
    return """
    <div class="dor-notice dor-notice-{status}">
        <strong>{title}</strong>
        <span>{text}</span>
    </div>
    """.format(
        status=status.lower(),
        title=_e(title),
        text=_e(text),
    )


def _chart_card(title, key):
    return """
    <article class="dor-chart-card">
        <h3>{title}</h3>
        <div class="dor-chart" data-chart-key="{key}">
            <div class="dor-chart-loading">Loading chart…</div>
        </div>
    </article>
    """.format(title=_e(title), key=_e(key))


def _type_card(summary):
    status = summary.status if summary.status in STATUS_DISPLAY_LABELS else "GREY"
    expected_questions = len(CHECKLIST_QUESTIONS.get(summary.checklist_type, []))
    completed_questions = cint(summary.yes_count) + cint(summary.no_count) + cint(summary.na_count)

    if summary.documents:
        gap_count = cint(summary.no_count) + cint(summary.unanswered_count)
    else:
        gap_count = expected_questions

    compliance_value = None if summary.compliance is None else max(0, min(100, flt(summary.compliance)))
    progress_width = compliance_value or 0
    compliance_label = _percent(compliance_value)
    display_label = STATUS_DISPLAY_LABELS.get(status, _("Pending"))
    icon = CHECKLIST_CARD_ICONS.get(summary.checklist_type, "•")

    return """
    <article
        class="dor-readiness-card dor-readiness-{status}"
        data-dor-checklist-type="{filter_value}"
        role="button"
        tabindex="0"
        title="{title_attr}"
        aria-label="Filter dashboard by {title_attr}"
    >
        <div class="dor-readiness-main">
            <div class="dor-readiness-icon" aria-hidden="true">{icon}</div>
            <div class="dor-readiness-content">
                <h3>{title}</h3>
                <div class="dor-readiness-score-row">
                    <strong>{compliance}</strong>
                    <span>{display_label}</span>
                </div>
            </div>
            <span class="dor-readiness-arrow" aria-hidden="true">›</span>
        </div>

        <div class="dor-readiness-progress" aria-hidden="true">
            <span style="width:{progress_width}%;"></span>
        </div>

        <div class="dor-readiness-footer">
            <div>
                <span>Completed</span>
                <strong>{completed} / {expected}</strong>
            </div>
            <div>
                <span>Control gaps</span>
                <strong class="dor-gap-value">{gaps}</strong>
            </div>
        </div>
    </article>
    """.format(
        status=status.lower(),
        filter_value=_e(summary.checklist_type),
        title_attr=_e(summary.checklist_type),
        title=_e(_short_checklist_label(summary.checklist_type)),
        icon=_e(icon),
        compliance=_e(compliance_label),
        display_label=_e(display_label),
        progress_width="{0:.1f}".format(progress_width),
        completed=_number(completed_questions),
        expected=_number(expected_questions),
        gaps=_number(gap_count),
    )


def _actions_table(actions):
    if not actions:
        return _empty_state(
            _("No failed requirements were recorded for the selected daily focus date."),
            "GREEN",
        )

    body = []
    for row in actions:
        body.append(
            """
            <tr>
                <td>{risk}</td>
                <td>{type}</td>
                <td class="dor-question-cell">{question}</td>
                <td>{user}</td>
                <td>{document}</td>
            </tr>
            """.format(
                risk=_status_pill(row.status, row.risk),
                type=_e(row.checklist_type),
                question=_e(row.question),
                user=_e(row.user),
                document=_document_link(row.document),
            )
        )

    return _table(
        [_('Risk'), _('Checklist Type'), _('Failed Requirement'), _('Responsible User'), _('Record')],
        "".join(body),
        "6% 19% 45% 17% 13%",
    )


def _type_summary_table(summaries):
    body = []
    for row in summaries:
        body.append(
            """
            <tr>
                <td>{type}</td>
                <td class="dor-number">{documents}</td>
                <td class="dor-number dor-positive">{yes_count}</td>
                <td class="dor-number dor-negative">{no_count}</td>
                <td class="dor-number">{na_count}</td>
                <td class="dor-number">{unanswered}</td>
                <td class="dor-number"><strong>{compliance}</strong></td>
                <td>{status}</td>
            </tr>
            """.format(
                type=_e(row.checklist_type),
                documents=_number(row.documents),
                yes_count=_number(row.yes_count),
                no_count=_number(row.no_count),
                na_count=_number(row.na_count),
                unanswered=_number(row.unanswered_count),
                compliance=_e(_percent(row.compliance)),
                status=_status_pill(row.status, row.status),
            )
        )

    return _table(
        [_('Checklist Type'), _('Records'), _('Yes'), _('No'), _('N/A'), _('Unanswered'), _('Compliance'), _('Status')],
        "".join(body),
        "28% 9% 8% 8% 8% 10% 14% 15%",
    )


def _hotspot_table(rows):
    if not rows:
        return _empty_state(_("No failed questions were found on the selected date."), "GREEN")

    body = []
    for row in rows:
        body.append(
            """
            <tr>
                <td>{status}</td>
                <td>{type}</td>
                <td class="dor-question-cell">{question}</td>
                <td class="dor-number"><strong>{count}</strong></td>
                <td>{date}</td>
            </tr>
            """.format(
                status=_status_pill(row.status, row.status),
                type=_e(row.checklist_type),
                question=_e(row.question),
                count=_number(row.count),
                date=_e(_date_label(row.latest_date)),
            )
        )

    return _table(
        [_('Risk'), _('Checklist'), _('Question'), _('Failures'), _('Latest')],
        "".join(body),
        "12% 23% 43% 10% 12%",
    )


def _latest_table(records):
    if not records:
        return _empty_state(_("No readiness records were found for the selected filters."), "GREY")

    body = []
    for row in records:
        body.append(
            """
            <tr>
                <td>{date}</td>
                <td>{record}</td>
                <td>{type}</td>
                <td>{user}</td>
                <td class="dor-number"><strong>{compliance}</strong></td>
                <td>{status}</td>
            </tr>
            """.format(
                date=_e(_date_label(row.date)),
                record=_document_link(row.name),
                type=_e(row.checklist_type),
                user=_e(row.full_name or row.user),
                compliance=_e(_percent(row.compliance)),
                status=_status_pill(row.status, row.status),
            )
        )

    return _table(
        [_('Date'), _('Record'), _('Checklist Type'), _('User'), _('Compliance'), _('Status')],
        "".join(body),
        "12% 19% 27% 20% 11% 11%",
    )


def _table(headers, body, widths):
    width_values = str(widths).split()
    colgroup = "".join(
        '<col style="width:{0};">'.format(_e(width))
        for width in width_values
    )
    head = "".join("<th>{0}</th>".format(_e(header)) for header in headers)
    return """
    <div class="dor-table-wrap">
        <table class="dor-table">
            <colgroup>{colgroup}</colgroup>
            <thead><tr>{head}</tr></thead>
            <tbody>{body}</tbody>
        </table>
    </div>
    """.format(colgroup=colgroup, head=head, body=body)


def _empty_state(message, status="GREY"):
    return """
    <div class="dor-empty dor-empty-{status}">
        <strong>{title}</strong>
        <span>{message}</span>
    </div>
    """.format(
        status=status.lower(),
        title=_e(_("No exception requiring display")),
        message=_e(message),
    )


def _status_pill(status, label):
    status = status if status in STATUS_COLORS else "GREY"
    return """
    <span class="dor-pill dor-pill-{status}">
        <span></span>{label}
    </span>
    """.format(status=status.lower(), label=_e(label))


def _document_link(name):
    route = "/app/daily-operational-readiness/{0}".format(quote(str(name or ""), safe=""))
    return '<a class="dor-doc-link" href="{0}">{1}</a>'.format(_e(route), _e(name or "—"))


def _short_checklist_label(value):
    replacements = {
        "Doctor and Nurse Availability": "Staff Availability",
        "Critical Service Availability": "Critical Services",
        "Laboratory and Diagnostic": "Lab & Diagnostics",
        "Operating Theatre and Surgery": "OT & Surgery",
        "Facility and Environment": "Facility & Environment",
        "Utilities and Fire Safety": "Utilities & Fire",
        "Incident and Patient Safety": "Incident & Safety",
        "Problems Requiring Action": "Action Problems",
    }
    return replacements.get(value, value)


def _number(value):
    return "{:,.0f}".format(flt(value))


def _percent(value):
    if value is None:
        return "—"
    return "{:,.1f}%".format(flt(value))


def _date_label(value):
    if not value:
        return "—"
    return getdate(value).strftime("%d-%m-%Y")


def _date_long_label(value):
    if not value:
        return "—"
    return getdate(value).strftime("%d %b %Y")


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _json_for_html(value):
    return json.dumps(value, ensure_ascii=False, default=str).replace("</", "<\\/")


def _dashboard_css():
    return r"""
    .dor-root {
        --dor-navy: #123a72;
        --dor-deep: #0b2a55;
        --dor-blue: #1686e5;
        --dor-green: #12b76a;
        --dor-amber: #f79009;
        --dor-red: #f04438;
        --dor-grey: #667085;
        --dor-border: #d9e5f1;
        --dor-muted: #667085;
        color: #152b47;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        padding: 14px;
        border-radius: 24px;
        background:
            radial-gradient(circle at 9% 0%, rgba(22, 134, 229, .08), transparent 28%),
            linear-gradient(180deg, #f5fbff 0%, #eef7fd 32%, #f7fbfe 100%);
    }

    .dor-root * { box-sizing: border-box; }

    .dor-hero {
        display: grid;
        grid-template-columns: 92px minmax(0, 1fr) auto;
        gap: 18px;
        align-items: center;
        padding: 8px 12px 18px;
    }

    .dor-hospital-mark {
        display: flex;
        width: 84px;
        height: 84px;
        align-items: center;
        justify-content: center;
        color: var(--dor-navy);
        border: 1px solid rgba(18, 58, 114, .12);
        border-radius: 24px;
        background: rgba(255,255,255,.88);
        box-shadow: 0 10px 30px rgba(18, 58, 114, .10);
    }

    .dor-hospital-mark svg { width: 66px; height: 66px; }

    .dor-eyebrow,
    .dor-kicker {
        display: block;
        color: var(--dor-blue);
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 1.2px;
        text-transform: uppercase;
    }

    .dor-hero h1 {
        margin: 5px 0 4px;
        color: var(--dor-deep);
        font-size: clamp(25px, 3vw, 38px);
        font-weight: 850;
        letter-spacing: -.8px;
    }

    .dor-hero p {
        margin: 0;
        color: #51708f;
        font-size: 13px;
        line-height: 1.5;
    }

    .dor-hero-date {
        min-width: 184px;
        padding: 13px 16px;
        border: 1px solid rgba(22, 134, 229, .14);
        border-radius: 16px;
        background: rgba(255,255,255,.78);
        text-align: right;
        box-shadow: 0 8px 24px rgba(18, 58, 114, .06);
    }

    .dor-hero-date span,
    .dor-hero-date small {
        display: block;
        color: var(--dor-muted);
        font-size: 9px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .55px;
    }

    .dor-hero-date strong {
        display: block;
        margin: 4px 0;
        color: var(--dor-navy);
        font-size: 17px;
        font-weight: 850;
    }

    .dor-notice {
        display: grid;
        grid-template-columns: minmax(180px, 260px) minmax(0, 1fr);
        gap: 16px;
        margin: 10px 0;
        padding: 12px 15px;
        border: 1px solid;
        border-left-width: 5px;
        border-radius: 12px;
        font-size: 11px;
        line-height: 1.5;
    }

    .dor-notice-green { color: #087443; background: #effcf5; border-color: #75dfaa; }
    .dor-notice-amber { color: #9a4d05; background: #fff8e8; border-color: #ffbd5b; }
    .dor-notice-red { color: #a4261f; background: #fff1f0; border-color: #ff9b95; }
    .dor-notice-grey { color: #475467; background: #f8fafc; border-color: #cbd5e1; }

    .dor-section {
        margin-top: 18px;
        padding: 18px;
        border: 1px solid rgba(18, 58, 114, .11);
        border-radius: 20px;
        background: rgba(255,255,255,.93);
        box-shadow: 0 10px 30px rgba(18, 58, 114, .06);
    }

    .dor-checklist-section {
        margin-top: 0;
        padding: 0;
        border: 0;
        background: transparent;
        box-shadow: none;
    }

    .dor-section-heading {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: flex-end;
        margin-bottom: 14px;
        padding: 0 2px;
    }

    .dor-section-heading h2 {
        margin: 4px 0 0;
        color: var(--dor-deep);
        font-size: 19px;
        font-weight: 850;
    }

    .dor-note {
        color: var(--dor-muted);
        font-size: 10px;
        text-align: right;
    }

    .dor-type-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 15px;
    }

    .dor-readiness-card {
        --card-a: #1686e5;
        --card-b: #0b67cf;
        --card-dark: #075bb8;
        --card-pale: #eaf6ff;
        position: relative;
        overflow: hidden;
        min-width: 0;
        border: 1px solid rgba(255,255,255,.75);
        border-radius: 22px;
        background: linear-gradient(135deg, var(--card-a) 0%, var(--card-b) 100%);
        box-shadow: 0 13px 30px rgba(18, 58, 114, .16);
        cursor: pointer;
        transition: transform .18s ease, box-shadow .18s ease, filter .18s ease;
        outline: none;
    }

    .dor-readiness-card:hover,
    .dor-readiness-card:focus-visible {
        transform: translateY(-4px);
        box-shadow: 0 18px 36px rgba(18, 58, 114, .23);
        filter: saturate(1.03);
    }

    .dor-readiness-green {
        --card-a: #2be58b;
        --card-b: #0ebf84;
        --card-dark: #08794f;
        --card-pale: #eafff5;
    }

    .dor-readiness-amber {
        --card-a: #ffbe2e;
        --card-b: #ff8a00;
        --card-dark: #a75200;
        --card-pale: #fff7df;
    }

    .dor-readiness-red {
        --card-a: #ff5474;
        --card-b: #ef3158;
        --card-dark: #ae1239;
        --card-pale: #fff0f4;
    }

    .dor-readiness-grey {
        --card-a: #8aa8c8;
        --card-b: #6485aa;
        --card-dark: #385a80;
        --card-pale: #eef4fa;
    }

    .dor-readiness-blue {
        --card-a: #27b7ef;
        --card-b: #0e79e7;
        --card-dark: #075bb8;
        --card-pale: #eaf6ff;
    }

    .dor-readiness-main {
        position: relative;
        z-index: 1;
        display: grid;
        grid-template-columns: 58px minmax(0, 1fr) 34px;
        gap: 12px;
        align-items: center;
        min-height: 142px;
        padding: 16px 15px 13px;
        color: #ffffff;
    }

    .dor-readiness-main::after {
        content: "";
        position: absolute;
        z-index: -1;
        right: -40px;
        bottom: -64px;
        width: 190px;
        height: 120px;
        border-radius: 50%;
        background: rgba(255,255,255,.15);
        transform: rotate(-8deg);
    }

    .dor-readiness-icon {
        display: flex;
        width: 58px;
        height: 58px;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        color: var(--card-dark);
        background: rgba(255,255,255,.96);
        box-shadow: 0 6px 18px rgba(18, 58, 114, .18);
        font-family: "Segoe UI Symbol", "Arial Unicode MS", sans-serif;
        font-size: 31px;
        font-weight: 900;
        line-height: 1;
    }

    .dor-readiness-content { min-width: 0; }

    .dor-readiness-content h3 {
        min-height: 37px;
        margin: 0 0 5px;
        color: #ffffff;
        font-size: 14px;
        font-weight: 850;
        line-height: 1.28;
        text-shadow: 0 1px 1px rgba(0,0,0,.08);
    }

    .dor-readiness-score-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        white-space: nowrap;
    }

    .dor-readiness-score-row strong {
        color: #ffffff;
        font-size: 38px;
        font-weight: 900;
        line-height: 1;
        letter-spacing: -1.3px;
    }

    .dor-readiness-score-row span {
        color: rgba(255,255,255,.94);
        font-size: 13px;
        font-weight: 800;
    }

    .dor-readiness-arrow {
        display: flex;
        width: 32px;
        height: 32px;
        align-items: center;
        justify-content: center;
        align-self: start;
        border-radius: 50%;
        color: var(--card-dark);
        background: rgba(255,255,255,.34);
        font-size: 25px;
        font-weight: 700;
        line-height: 1;
    }

    .dor-readiness-progress {
        height: 5px;
        background: rgba(255,255,255,.28);
    }

    .dor-readiness-progress span {
        display: block;
        height: 100%;
        border-radius: 0 999px 999px 0;
        background: rgba(255,255,255,.94);
    }

    .dor-readiness-footer {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        min-height: 75px;
        padding: 12px 14px;
        color: var(--dor-deep);
        background: linear-gradient(180deg, rgba(255,255,255,.98), var(--card-pale));
    }

    .dor-readiness-footer > div {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-width: 0;
    }

    .dor-readiness-footer > div + div {
        padding-left: 14px;
        border-left: 1px solid rgba(18,58,114,.12);
    }

    .dor-readiness-footer span {
        color: #55718e;
        font-size: 9px;
        font-weight: 750;
        text-transform: uppercase;
        letter-spacing: .35px;
    }

    .dor-readiness-footer strong {
        margin-top: 3px;
        color: var(--dor-deep);
        font-size: 17px;
        font-weight: 850;
    }

    .dor-readiness-footer .dor-gap-value { color: #d92d20; }

    .dor-overall-section { background: rgba(255,255,255,.96); }

    .dor-overall-panel {
        --overall: var(--dor-grey);
        --overall-soft: #f2f4f7;
        display: grid;
        grid-template-columns: minmax(220px, .95fr) minmax(320px, 1.55fr) minmax(130px, .45fr);
        gap: 22px;
        align-items: center;
        padding: 18px 20px;
        border: 1px solid rgba(18, 58, 114, .13);
        border-left: 7px solid var(--overall);
        border-radius: 18px;
        background: linear-gradient(135deg, #ffffff, var(--overall-soft));
        box-shadow: 0 8px 24px rgba(18, 58, 114, .07);
    }

    .dor-overall-panel-green { --overall: #12b76a; --overall-soft: #eafbf3; }
    .dor-overall-panel-amber { --overall: #f79009; --overall-soft: #fff6e5; }
    .dor-overall-panel-red { --overall: #f04438; --overall-soft: #fff0ef; }
    .dor-overall-panel-grey { --overall: #667085; --overall-soft: #f2f4f7; }
    .dor-overall-panel-blue { --overall: #1686e5; --overall-soft: #eaf6ff; }

    .dor-overall-kicker {
        color: var(--overall);
        font-size: 9px;
        font-weight: 900;
        text-transform: uppercase;
        letter-spacing: .7px;
    }

    .dor-overall-title-row {
        display: flex;
        align-items: baseline;
        gap: 9px;
        margin: 5px 0 6px;
    }

    .dor-overall-title-row strong {
        color: var(--overall);
        font-size: 28px;
        font-weight: 900;
        letter-spacing: -.7px;
    }

    .dor-overall-title-row span {
        color: #344054;
        font-size: 12px;
        font-weight: 800;
    }

    .dor-overall-copy p {
        margin: 0;
        color: #667085;
        font-size: 10.5px;
        line-height: 1.5;
    }

    .dor-overall-progress-labels {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 7px;
        color: #344054;
        font-size: 11px;
        font-weight: 800;
    }

    .dor-overall-progress-labels strong { color: var(--overall); }

    .dor-overall-progress {
        height: 15px;
        overflow: hidden;
        border-radius: 999px;
        background: #dbe9f5;
        box-shadow: inset 0 1px 2px rgba(18,58,114,.08);
    }

    .dor-overall-progress span {
        display: block;
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, rgba(255,255,255,.28), var(--overall));
        box-shadow: 0 0 14px rgba(18, 58, 114, .12);
    }

    .dor-overall-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 5px 15px;
        margin-top: 8px;
        color: #667085;
        font-size: 9.5px;
    }

    .dor-overall-meta b { color: var(--dor-deep); }

    .dor-overall-score {
        text-align: right;
    }

    .dor-overall-score strong {
        display: block;
        color: var(--overall);
        font-size: 42px;
        font-weight: 900;
        line-height: 1;
        letter-spacing: -1.4px;
    }

    .dor-overall-score span,
    .dor-overall-score small {
        display: block;
    }

    .dor-overall-score span {
        margin-top: 4px;
        color: #344054;
        font-size: 11px;
        font-weight: 800;
    }

    .dor-overall-score small {
        margin-top: 4px;
        color: #667085;
        font-size: 8.5px;
    }

    .dor-action-section {
        border-color: #ffd3cf;
        background: linear-gradient(180deg, rgba(255,255,255,.96), #fff8f7);
    }

    .dor-chart-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
    }

    .dor-chart-card {
        min-width: 0;
        padding: 13px;
        border: 1px solid #e0eaf3;
        border-radius: 14px;
        background: #fbfdff;
    }

    .dor-chart-card h3 {
        margin: 0 0 8px;
        color: var(--dor-deep);
        font-size: 12px;
        font-weight: 850;
    }

    .dor-chart { min-height: 250px; overflow: hidden; }

    .dor-chart-loading,
    .dor-chart-empty {
        display: flex;
        min-height: 250px;
        align-items: center;
        justify-content: center;
        color: var(--dor-muted);
        font-size: 11px;
    }

    .dor-table-wrap {
        width: 100%;
        overflow-x: auto;
        border: 1px solid var(--dor-border);
        border-radius: 12px;
        background: #ffffff;
    }

    .dor-table {
        width: 100%;
        min-width: 760px;
        border-collapse: collapse;
        table-layout: fixed;
        font-size: 10px;
    }

    .dor-table th {
        padding: 9px 8px;
        color: #ffffff;
        background: var(--dor-deep);
        border-right: 1px solid rgba(255,255,255,.15);
        font-size: 9px;
        font-weight: 800;
        text-align: left;
        text-transform: uppercase;
        letter-spacing: .3px;
    }

    .dor-table td {
        padding: 8px;
        border-top: 1px solid #e8edf3;
        color: #344054;
        vertical-align: top;
        line-height: 1.45;
        word-break: break-word;
    }

    .dor-table tbody tr:nth-child(even) { background: #f8fafc; }
    .dor-table tbody tr:hover { background: #f0f7ff; }
    .dor-number { text-align: right; }
    .dor-positive { color: var(--dor-green) !important; }
    .dor-negative { color: var(--dor-red) !important; }
    .dor-question-cell { font-weight: 650; color: #1d2939 !important; }

    .dor-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 8px;
        border-radius: 999px;
        font-size: 8.5px;
        font-weight: 800;
        white-space: nowrap;
    }

    .dor-pill span {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: currentColor;
    }

    .dor-pill-green { color: #087443; background: #dff8eb; }
    .dor-pill-amber { color: #9a4d05; background: #fff0cf; }
    .dor-pill-red { color: #a4261f; background: #ffe0de; }
    .dor-pill-grey { color: #475467; background: #e8edf2; }

    .dor-doc-link {
        color: var(--dor-blue);
        font-weight: 750;
        text-decoration: none;
    }

    .dor-doc-link:hover { text-decoration: underline; }

    .dor-empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 150px;
        padding: 18px;
        border: 1px dashed;
        border-radius: 12px;
        text-align: center;
    }

    .dor-empty strong { margin-bottom: 5px; }
    .dor-empty-green { color: #087443; background: #effcf5; border-color: #75dfaa; }
    .dor-empty-amber { color: #9a4d05; background: #fff8e8; border-color: #ffbd5b; }
    .dor-empty-red { color: #a4261f; background: #fff1f0; border-color: #ff9b95; }
    .dor-empty-grey { color: #667085; background: #f8fafc; border-color: #cbd5e1; }

    .dor-two-column {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
    }

    .dor-footer {
        margin-top: 18px;
        padding: 12px 4px 2px;
        border-top: 1px solid var(--dor-border);
        color: var(--dor-muted);
        font-size: 9.5px;
        line-height: 1.6;
        text-align: center;
    }

    @media (max-width: 1250px) {
        .dor-type-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }

    @media (max-width: 980px) {
        .dor-type-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .dor-hero { grid-template-columns: 74px minmax(0, 1fr); }
        .dor-hospital-mark { width: 68px; height: 68px; border-radius: 20px; }
        .dor-hospital-mark svg { width: 54px; height: 54px; }
        .dor-hero-date { grid-column: 1 / -1; text-align: left; }
        .dor-overall-panel,
        .dor-two-column,
        .dor-chart-grid { grid-template-columns: 1fr; }
        .dor-overall-score { text-align: left; }
    }

    @media (max-width: 620px) {
        .dor-root { padding: 9px; border-radius: 16px; }
        .dor-hero { grid-template-columns: 1fr; padding: 6px 4px 14px; }
        .dor-hospital-mark { display: none; }
        .dor-hero-date { min-width: 0; }
        .dor-type-grid { grid-template-columns: 1fr; }
        .dor-section { padding: 14px; }
        .dor-checklist-section { padding: 0; }
        .dor-section-heading { align-items: flex-start; flex-direction: column; }
        .dor-note { text-align: left; }
        .dor-notice { grid-template-columns: 1fr; gap: 3px; }
        .dor-overall-panel { padding: 15px; gap: 14px; }
    }

    @media print {
        .dor-root {
            padding: 0;
            background: #ffffff;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }

        .dor-readiness-card,
        .dor-overall-panel,
        .dor-section,
        .dor-chart-card {
            box-shadow: none !important;
            break-inside: avoid;
        }

        .dor-readiness-card:hover { transform: none; }
        .dor-type-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .dor-two-column { grid-template-columns: 1fr; }
    }
    """
