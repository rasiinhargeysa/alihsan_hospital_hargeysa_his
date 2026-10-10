# Copyright (c) 2026, Al-Ihsan Hospital and contributors
# For license information, please see license.txt

"""Queue Time Management Script Report and Main Hospital Dashboard source.

The report follows one patient journey per selected day. If a patient has more
than one Que row that day, the earliest queue timestamp is retained, matching
the intent of the original GROUP BY patient query without relying on
non-deterministic SQL grouping.

For every patient, the first chronological event on or after the previous stage
is selected:

    Queue -> Patient Encounter -> Sample Collection -> Lab Result

This avoids duplicate rows when any source DocType contains multiple records for
one patient on the same day.
"""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, time as time_type, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, get_datetime, now_datetime


REPORT_NAME = "Queue Time Management"
QUEUE_DOCTYPE = "Que"
ENCOUNTER_DOCTYPE = "Patient Encounter"
SAMPLE_DOCTYPE = "Sample Collection"
LAB_DOCTYPE = "Lab Result"

QUEUE_TARGET_MINUTES = 20
QUEUE_CRITICAL_MINUTES = 30

STANDARD_FIELDS = {
    "name",
    "creation",
    "modified",
    "owner",
    "modified_by",
    "docstatus",
    "idx",
}

QUEUE_FIELD_MAP = {
    "date": ["date", "queue_date", "creation"],
    "time": ["time", "queue_time", "creation"],
    "patient": ["patient"],
    "patient_name": ["patient_name"],
    "practitioner": ["practitioner", "doctor"],
    "department": ["department", "medical_department"],
}

EVENT_FIELD_MAPS = {
    ENCOUNTER_DOCTYPE: {
        "date": ["encounter_date", "date", "creation"],
        "time": ["encounter_time", "time", "creation"],
        "patient": ["patient"],
    },
    SAMPLE_DOCTYPE: {
        "date": ["date", "sample_collection_date", "collection_date", "creation"],
        "time": ["time", "sample_collection_time", "collection_time", "creation"],
        "patient": ["patient"],
    },
    LAB_DOCTYPE: {
        "date": ["date", "result_date", "posting_date", "creation"],
        "time": ["time", "result_time", "creation"],
        "patient": ["patient"],
    },
}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    source = get_dashboard_source(filters)

    if not source.get("available"):
        frappe.throw(source.get("reason") or _("Queue Time Management data is unavailable."))

    columns = get_columns()
    data = source.get("rows") or []
    chart = get_chart(source)
    summary = get_report_summary(source)

    return columns, data, None, chart, summary, 1


def get_columns():
    return [
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
        {
            "label": _("Patient"),
            "fieldname": "patient",
            "fieldtype": "Link",
            "options": "Patient",
            "width": 110,
        },
        {"label": _("Patient Name"), "fieldname": "patient_name", "fieldtype": "Data", "width": 220},
        {
            "label": _("Doctor"),
            "fieldname": "practitioner",
            "fieldtype": "Link",
            "options": "Healthcare Practitioner",
            "width": 220,
        },
        {"label": _("Queue Time"), "fieldname": "queue_time", "fieldtype": "Data", "width": 105},
        {"label": _("Encounter Time"), "fieldname": "encounter_time", "fieldtype": "Data", "width": 115},
        {
            "label": _("Sample Collection Time"),
            "fieldname": "sample_collection_time",
            "fieldtype": "Data",
            "width": 150,
        },
        {"label": _("Lab Result Time"), "fieldname": "lab_result_time", "fieldtype": "Data", "width": 120},
        {
            "label": _("Time Diff Queue to Encounter"),
            "fieldname": "queue_to_encounter",
            "fieldtype": "Data",
            "width": 205,
        },
        {
            "label": _("Time Diff Encounter to Collection"),
            "fieldname": "encounter_to_collection",
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "label": _("Time Diff Collection to Result"),
            "fieldname": "collection_to_result",
            "fieldtype": "Data",
            "width": 215,
        },
        {"label": _("Status"), "fieldname": "queue_status", "fieldtype": "Data", "width": 120},
    ]


def get_dashboard_source(filters=None):
    """Return the normalized source used by both the report and Desk Page."""
    filters = frappe._dict(filters or {})
    selected_date = getdate(
        filters.get("date")
        or filters.get("queue_date")
        or filters.get("to_date")
        or now_datetime().date()
    )

    queue_fields = _doctype_fields(QUEUE_DOCTYPE)
    if not queue_fields:
        return _unavailable(_("DocType Que was not found."))

    queue_schema = _resolve_fields(queue_fields, QUEUE_FIELD_MAP)
    required = ("date", "time", "patient")
    missing = [role for role in required if not queue_schema.get(role)]
    if missing:
        return _unavailable(
            _("Que is missing fields needed for Queue Time Management: {0}.").format(
                ", ".join(missing)
            )
        )

    queue_filters = _date_filter(queue_schema["date"], selected_date)
    if filters.get("practitioner") and queue_schema.get("practitioner"):
        queue_filters[queue_schema["practitioner"]] = filters.practitioner
    if filters.get("department") and queue_schema.get("department"):
        queue_filters[queue_schema["department"]] = filters.department
    if filters.get("patient"):
        queue_filters[queue_schema["patient"]] = filters.patient

    warnings = []
    if filters.get("department") and not queue_schema.get("department"):
        warnings.append(_("The Department filter could not be applied because Que has no department field."))
    if filters.get("practitioner") and not queue_schema.get("practitioner"):
        warnings.append(_("The Practitioner filter could not be applied because Que has no practitioner field."))

    selected_queue_fields = ["name"]
    for fieldname in queue_schema.values():
        if fieldname not in selected_queue_fields:
            selected_queue_fields.append(fieldname)

    queue_rows = frappe.get_all(
        QUEUE_DOCTYPE,
        filters=queue_filters,
        fields=selected_queue_fields,
        limit_page_length=0,
    )

    queue_by_patient = {}
    invalid_queue_times = 0
    duplicate_queue_rows = 0

    for raw in queue_rows:
        row = frappe._dict(raw)
        patient = row.get(queue_schema["patient"])
        queue_at = _combine_datetime(
            row.get(queue_schema["date"]),
            row.get(queue_schema["time"]),
        )
        if not patient or not queue_at:
            invalid_queue_times += 1
            continue

        record = frappe._dict(
            name=row.get("name"),
            date=selected_date,
            patient=patient,
            patient_name=row.get(queue_schema.get("patient_name")) if queue_schema.get("patient_name") else None,
            practitioner=row.get(queue_schema.get("practitioner")) if queue_schema.get("practitioner") else None,
            department=row.get(queue_schema.get("department")) if queue_schema.get("department") else None,
            queue_at=queue_at,
        )

        existing = queue_by_patient.get(patient)
        if existing:
            duplicate_queue_rows += 1
            if queue_at < existing.queue_at:
                queue_by_patient[patient] = record
        else:
            queue_by_patient[patient] = record

    if invalid_queue_times:
        warnings.append(
            _("{0} Que row(s) were skipped because patient or queue time was missing.").format(
                invalid_queue_times
            )
        )
    if duplicate_queue_rows:
        warnings.append(
            _("{0} additional Que row(s) were collapsed to the earliest queue event per patient for the selected day.").format(
                duplicate_queue_rows
            )
        )

    patient_ids = list(queue_by_patient)
    if not patient_ids:
        return _empty_source(selected_date, warnings)

    _fill_patient_names(queue_by_patient)

    encounter_map, encounter_warnings = _load_event_map(
        ENCOUNTER_DOCTYPE, selected_date, patient_ids
    )
    sample_map, sample_warnings = _load_event_map(
        SAMPLE_DOCTYPE, selected_date, patient_ids
    )
    lab_map, lab_warnings = _load_event_map(LAB_DOCTYPE, selected_date, patient_ids)
    warnings.extend(encounter_warnings + sample_warnings + lab_warnings)

    now_value = now_datetime()
    today = now_value.date()
    rows = []
    doctor_stats = defaultdict(
        lambda: {
            "patients": 0,
            "encountered": 0,
            "waiting": 0,
            "over_target": 0,
            "critical": 0,
            "wait_values": [],
        }
    )
    hourly = defaultdict(lambda: {"queued": 0, "encountered": 0})
    wait_buckets = Counter()

    for record in sorted(queue_by_patient.values(), key=lambda item: item.queue_at, reverse=True):
        encounter_at = _first_at_or_after(encounter_map.get(record.patient), record.queue_at)
        sample_floor = encounter_at or record.queue_at
        sample_at = _first_at_or_after(sample_map.get(record.patient), sample_floor)
        result_floor = sample_at or encounter_at or record.queue_at
        lab_result_at = _first_at_or_after(lab_map.get(record.patient), result_floor)

        queue_to_encounter_minutes = _minutes_between(record.queue_at, encounter_at)
        encounter_to_collection_minutes = _minutes_between(encounter_at, sample_at)
        collection_to_result_minutes = _minutes_between(sample_at, lab_result_at)

        if encounter_at:
            effective_wait = queue_to_encounter_minutes
            queue_status = _wait_status(effective_wait)
        elif selected_date == today:
            effective_wait = _minutes_between(record.queue_at, now_value)
            queue_status = _wait_status(effective_wait, waiting=True)
        elif selected_date < today:
            effective_wait = None
            queue_status = "RED"
        else:
            effective_wait = None
            queue_status = "GREY"

        over_target = bool(
            (effective_wait is not None and effective_wait > QUEUE_TARGET_MINUTES)
            or (not encounter_at and selected_date < today)
        )
        critical = bool(
            (effective_wait is not None and effective_wait > QUEUE_CRITICAL_MINUTES)
            or (not encounter_at and selected_date < today)
        )

        if effective_wait is None:
            wait_buckets["No Encounter"] += 1
        elif effective_wait > QUEUE_CRITICAL_MINUTES:
            wait_buckets["> 30 min"] += 1
        elif effective_wait > QUEUE_TARGET_MINUTES:
            wait_buckets["21–30 min"] += 1
        else:
            wait_buckets["≤ 20 min"] += 1

        doctor = record.practitioner or _("No Doctor Assigned")
        bucket = doctor_stats[doctor]
        bucket["patients"] += 1
        if encounter_at:
            bucket["encountered"] += 1
        else:
            bucket["waiting"] += 1
        if over_target:
            bucket["over_target"] += 1
        if critical:
            bucket["critical"] += 1
        if effective_wait is not None:
            bucket["wait_values"].append(effective_wait)

        queue_hour = record.queue_at.strftime("%H:00")
        hourly[queue_hour]["queued"] += 1
        if encounter_at:
            encounter_hour = encounter_at.strftime("%H:00")
            hourly[encounter_hour]["encountered"] += 1

        rows.append(
            {
                "date": str(selected_date),
                "patient": record.patient,
                "patient_name": record.patient_name or record.patient,
                "practitioner": record.practitioner or "",
                "department": record.department or "",
                "queue_time": _clock(record.queue_at),
                "encounter_time": _clock(encounter_at),
                "sample_collection_time": _clock(sample_at),
                "lab_result_time": _clock(lab_result_at),
                "queue_to_encounter": _duration_label(queue_to_encounter_minutes),
                "encounter_to_collection": _duration_label(encounter_to_collection_minutes),
                "collection_to_result": _duration_label(collection_to_result_minutes),
                "queue_status": _status_label(queue_status, encounter_at),
                "queue_status_code": queue_status,
                "queue_to_encounter_minutes": queue_to_encounter_minutes,
                "encounter_to_collection_minutes": encounter_to_collection_minutes,
                "collection_to_result_minutes": collection_to_result_minutes,
                "effective_wait_minutes": effective_wait,
                "over_target": 1 if over_target else 0,
                "critical_wait": 1 if critical else 0,
            }
        )

    effective_waits = [
        flt(row.get("effective_wait_minutes"))
        for row in rows
        if row.get("effective_wait_minutes") is not None
    ]
    queue_to_encounter_values = [
        flt(row.get("queue_to_encounter_minutes"))
        for row in rows
        if row.get("queue_to_encounter_minutes") is not None
    ]
    encounter_to_collection_values = [
        flt(row.get("encounter_to_collection_minutes"))
        for row in rows
        if row.get("encounter_to_collection_minutes") is not None
    ]
    collection_to_result_values = [
        flt(row.get("collection_to_result_minutes"))
        for row in rows
        if row.get("collection_to_result_minutes") is not None
    ]

    total = len(rows)
    encountered = sum(1 for row in rows if row.get("encounter_time") != "yet")
    sampled = sum(1 for row in rows if row.get("sample_collection_time") != "yet")
    resulted = sum(1 for row in rows if row.get("lab_result_time") != "yet")
    over_target_count = sum(cint(row.get("over_target")) for row in rows)
    critical_count = sum(cint(row.get("critical_wait")) for row in rows)

    by_doctor = []
    for doctor, values in doctor_stats.items():
        waits = values.pop("wait_values")
        by_doctor.append(
            {
                "practitioner": doctor,
                **values,
                "average_wait": _average(waits),
                "longest_wait": max(waits) if waits else None,
            }
        )
    by_doctor.sort(
        key=lambda row: (
            -cint(row.get("critical")),
            -cint(row.get("over_target")),
            -cint(row.get("patients")),
            str(row.get("practitioner")),
        )
    )

    hourly_rows = [
        {
            "period": hour,
            "queued": values["queued"],
            "encountered": values["encountered"],
        }
        for hour, values in sorted(hourly.items())
    ]

    if not total:
        status = "GREY"
    elif critical_count:
        status = "RED"
    elif over_target_count:
        status = "AMBER"
    else:
        status = "GREEN"

    return {
        "available": True,
        "label": "Queue Time",
        "status": status,
        "queue_date": str(selected_date),
        "target_minutes": QUEUE_TARGET_MINUTES,
        "critical_minutes": QUEUE_CRITICAL_MINUTES,
        "totals": {
            "patients": total,
            "encountered": encountered,
            "waiting": total - encountered,
            "samples_collected": sampled,
            "lab_results": resulted,
            "over_target": over_target_count,
            "critical_waits": critical_count,
            "average_wait": _average(effective_waits),
            "average_queue_to_encounter": _average(queue_to_encounter_values),
            "longest_wait": max(effective_waits) if effective_waits else None,
            "average_encounter_to_collection": _average(encounter_to_collection_values),
            "average_collection_to_result": _average(collection_to_result_values),
        },
        "rows": rows,
        "recent": rows[:25],
        "by_doctor": by_doctor[:12],
        "hourly": hourly_rows,
        "wait_buckets": [
            {"label": label, "value": wait_buckets.get(label, 0)}
            for label in ("≤ 20 min", "21–30 min", "> 30 min", "No Encounter")
        ],
        "report": REPORT_NAME,
        "report_exists": bool(frappe.db.exists("Report", REPORT_NAME)),
        "warnings": warnings,
    }


def get_chart(source):
    doctor_rows = source.get("by_doctor") or []
    if not doctor_rows:
        return None

    return {
        "data": {
            "labels": [row.get("practitioner") for row in doctor_rows],
            "datasets": [
                {
                    "name": _("Average Wait"),
                    "values": [round(flt(row.get("average_wait")), 1) for row in doctor_rows],
                },
                {
                    "name": _("Longest Wait"),
                    "values": [round(flt(row.get("longest_wait")), 1) for row in doctor_rows],
                },
            ],
        },
        "type": "bar",
        "height": 320,
        "colors": ["#0ea5e9", "#dc2626"],
        "axisOptions": {"shortenYAxisNumbers": 1},
        "barOptions": {"spaceRatio": 0.35},
    }


def get_report_summary(source):
    totals = source.get("totals") or {}
    return [
        {
            "label": _("Patients Queued"),
            "value": cint(totals.get("patients")),
            "datatype": "Int",
            "indicator": "Blue",
        },
        {
            "label": _("Encountered"),
            "value": cint(totals.get("encountered")),
            "datatype": "Int",
            "indicator": "Green",
        },
        {
            "label": _("Waiting / No Encounter"),
            "value": cint(totals.get("waiting")),
            "datatype": "Int",
            "indicator": "Orange" if totals.get("waiting") else "Green",
        },
        {
            "label": _("Average Queue Wait"),
            "value": _duration_label(totals.get("average_wait")),
            "datatype": "Data",
            "indicator": _indicator_for_minutes(totals.get("average_wait")),
        },
        {
            "label": _("Longest Queue Wait"),
            "value": _duration_label(totals.get("longest_wait")),
            "datatype": "Data",
            "indicator": _indicator_for_minutes(totals.get("longest_wait")),
        },
        {
            "label": _("Over 20 Minutes"),
            "value": cint(totals.get("over_target")),
            "datatype": "Int",
            "indicator": "Red" if totals.get("critical_waits") else ("Orange" if totals.get("over_target") else "Green"),
        },
        {
            "label": _("Samples Collected"),
            "value": cint(totals.get("samples_collected")),
            "datatype": "Int",
            "indicator": "Blue",
        },
        {
            "label": _("Lab Results Available"),
            "value": cint(totals.get("lab_results")),
            "datatype": "Int",
            "indicator": "Blue",
        },
    ]


def _doctype_fields(doctype):
    if not frappe.db.exists("DocType", doctype):
        return set()
    meta = frappe.get_meta(doctype)
    return STANDARD_FIELDS.union({df.fieldname for df in meta.fields if df.fieldname})


def _resolve_fields(fields, mapping):
    resolved = {}
    for role, candidates in mapping.items():
        for candidate in candidates:
            if candidate in fields:
                resolved[role] = candidate
                break
    return resolved


def _date_filter(fieldname, selected_date):
    if fieldname in {"creation", "modified"}:
        start = "{0} 00:00:00".format(selected_date)
        end = "{0} 23:59:59.999999".format(selected_date)
        return {fieldname: ["between", [start, end]]}
    return {fieldname: selected_date}


def _load_event_map(doctype, selected_date, patient_ids):
    warnings = []
    fields = _doctype_fields(doctype)
    if not fields:
        return {}, [_("DocType {0} was not found; that stage is shown as yet.").format(doctype)]

    schema = _resolve_fields(fields, EVENT_FIELD_MAPS[doctype])
    missing = [role for role in ("date", "time", "patient") if not schema.get(role)]
    if missing:
        return {}, [
            _("{0} is missing fields for: {1}; that stage is shown as yet.").format(
                doctype, ", ".join(missing)
            )
        ]

    filters = _date_filter(schema["date"], selected_date)
    if patient_ids:
        filters[schema["patient"]] = ["in", patient_ids]
    if "docstatus" in fields:
        filters["docstatus"] = ["!=", 2]

    selected_fields = ["name"]
    for fieldname in schema.values():
        if fieldname not in selected_fields:
            selected_fields.append(fieldname)

    rows = frappe.get_all(
        doctype,
        filters=filters,
        fields=selected_fields,
        limit_page_length=0,
    )

    event_map = defaultdict(list)
    invalid = 0
    for raw in rows:
        row = frappe._dict(raw)
        patient = row.get(schema["patient"])
        event_at = _combine_datetime(row.get(schema["date"]), row.get(schema["time"]))
        if not patient or not event_at:
            invalid += 1
            continue
        event_map[patient].append(event_at)

    for values in event_map.values():
        values.sort()

    if invalid:
        warnings.append(
            _("{0}: {1} row(s) were ignored because patient or time was missing.").format(
                doctype, invalid
            )
        )

    return dict(event_map), warnings


def _fill_patient_names(queue_by_patient):
    missing = [
        patient
        for patient, record in queue_by_patient.items()
        if not record.get("patient_name")
    ]
    if not missing:
        return

    patient_fields = _doctype_fields("Patient")
    if not patient_fields or "patient_name" not in patient_fields:
        return

    rows = frappe.get_all(
        "Patient",
        filters={"name": ["in", missing]},
        fields=["name", "patient_name"],
        limit_page_length=0,
    )
    names = {row.get("name"): row.get("patient_name") for row in rows}
    for patient in missing:
        queue_by_patient[patient].patient_name = names.get(patient) or patient


def _combine_datetime(date_value, time_value):
    if isinstance(time_value, datetime):
        return time_value
    if isinstance(date_value, datetime) and (
        time_value in (None, "") or time_value == date_value
    ):
        return date_value

    try:
        base_date = getdate(date_value)
    except Exception:
        base_date = None

    if isinstance(time_value, time_type) and base_date:
        return datetime.combine(base_date, time_value)
    if isinstance(time_value, timedelta) and base_date:
        return datetime.combine(base_date, time_type.min) + time_value

    text = str(time_value or "").strip()
    if not text:
        if isinstance(date_value, datetime):
            return date_value
        return None

    try:
        if " " in text or "T" in text:
            parsed = get_datetime(text)
            if parsed:
                return parsed
    except Exception:
        pass

    if not base_date:
        try:
            parsed = get_datetime(text)
            return parsed
        except Exception:
            return None

    try:
        return get_datetime("{0} {1}".format(base_date, text))
    except Exception:
        return None


def _first_at_or_after(values, floor):
    if not values:
        return None
    index = bisect_left(values, floor)
    return values[index] if index < len(values) else None


def _minutes_between(start, end):
    if not start or not end:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return None
    return round(seconds / 60.0, 2)


def _average(values):
    values = [flt(value) for value in values if value is not None]
    return round(sum(values) / len(values), 2) if values else None


def _clock(value):
    return value.strftime("%H:%M:%S") if value else "yet"


def _duration_label(minutes):
    if minutes is None:
        return "yet"
    total = max(0, int(round(flt(minutes))))
    hours, remaining = divmod(total, 60)
    if hours:
        return _("{0} hour(s) {1} minute(s)").format(hours, remaining)
    return _("{0} minute(s)").format(remaining)


def _wait_status(minutes, waiting=False):
    if minutes is None:
        return "GREY"
    if minutes > QUEUE_CRITICAL_MINUTES:
        return "RED"
    if minutes > QUEUE_TARGET_MINUTES:
        return "AMBER"
    return "BLUE" if waiting else "GREEN"


def _status_label(status, encounter_at):
    if encounter_at:
        return {
            "GREEN": _("Within target"),
            "AMBER": _("Delayed"),
            "RED": _("Critical delay"),
            "GREY": _("Unknown"),
        }.get(status, status)
    return {
        "BLUE": _("Waiting"),
        "AMBER": _("Waiting over target"),
        "RED": _("No encounter / critical wait"),
        "GREY": _("Not started"),
    }.get(status, status)


def _indicator_for_minutes(minutes):
    if minutes is None:
        return "Grey"
    if flt(minutes) > QUEUE_CRITICAL_MINUTES:
        return "Red"
    if flt(minutes) > QUEUE_TARGET_MINUTES:
        return "Orange"
    return "Green"


def _empty_source(selected_date, warnings=None):
    return {
        "available": True,
        "label": "Queue Time",
        "status": "GREY",
        "queue_date": str(selected_date),
        "target_minutes": QUEUE_TARGET_MINUTES,
        "critical_minutes": QUEUE_CRITICAL_MINUTES,
        "totals": {
            "patients": 0,
            "encountered": 0,
            "waiting": 0,
            "samples_collected": 0,
            "lab_results": 0,
            "over_target": 0,
            "critical_waits": 0,
            "average_wait": None,
            "average_queue_to_encounter": None,
            "longest_wait": None,
            "average_encounter_to_collection": None,
            "average_collection_to_result": None,
        },
        "rows": [],
        "recent": [],
        "by_doctor": [],
        "hourly": [],
        "wait_buckets": [
            {"label": label, "value": 0}
            for label in ("≤ 20 min", "21–30 min", "> 30 min", "No Encounter")
        ],
        "report": REPORT_NAME,
        "report_exists": bool(frappe.db.exists("Report", REPORT_NAME)),
        "warnings": warnings or [],
    }


def _unavailable(reason):
    return {
        "available": False,
        "label": "Queue Time",
        "reason": reason,
        "warnings": [],
        "status": "GREY",
        "report": REPORT_NAME,
        "report_exists": bool(frappe.db.exists("Report", REPORT_NAME)),
    }
