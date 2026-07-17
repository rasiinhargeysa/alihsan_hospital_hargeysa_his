import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


# ─── Columns ────────────────────────────────────────────────────────────────

def get_columns():
    return [
        {
            "label": _("Date"),
            "fieldname": "date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Patient Name"),
            "fieldname": "full_name",
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "label": _("Age"),
            "fieldname": "age",
            "fieldtype": "Data",
            "width": 60,
        },
        {
            "label": _("Sex"),
            "fieldname": "sex",
            "fieldtype": "Data",
            "width": 70,
        },
        {
            "label": _("Mobile"),
            "fieldname": "mobile",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Doctor"),
            "fieldname": "doctor",
            "fieldtype": "Link",
            "options": "Healthcare Practitioner",
            "width": 150,
        },
        {
            "label": _("VA Right Eye"),
            "fieldname": "right_eye",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("VA Left Eye"),
            "fieldname": "left_eye",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("IOP Right Eye"),
            "fieldname": "r_eye",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("IOP Left Eye"),
            "fieldname": "l_eye",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("Eye Pain"),
            "fieldname": "history_of_eye_pain_and_discomfort",
            "fieldtype": "Data",
            "width": 90,
        },
        {
            "label": _("Vision Difficulty"),
            "fieldname": "difficult_in_vision_near_far",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Redness / Itching"),
            "fieldname": "history_of_redness_tching_in_eyes",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("Watering / Discharge"),
            "fieldname": "history_of_watering_or_discharge",
            "fieldtype": "Data",
            "width": 140,
        },
        {
            "label": _("Wears Glasses"),
            "fieldname": "do_you_wear_glasses_or_contact_lenses",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("Chronic Disease (DM/HTN)"),
            "fieldname": "history_of_chronic_diseases_dmhtn",
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "label": _("Vision Status"),
            "fieldname": "overall_vision_status",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("Diagnosis"),
            "fieldname": "diagnosis",
            "fieldtype": "Link",
            "options": "Diagnosis",
            "width": 150,
        },
        {
            "label": _("Referral"),
            "fieldname": "referral",
            "fieldtype": "Data",
            "width": 90,
        },
        {
            "label": _("Comments"),
            "fieldname": "referral__comments",
            "fieldtype": "Small Text",
            "width": 200,
        },
    ]


# ─── Data ────────────────────────────────────────────────────────────────────

def get_data(filters):
    conditions, values = build_conditions(filters)

    query = f"""
        SELECT
            vs.date,
            vs.full_name,
            vs.age,
            vs.sex,
            vs.mobile,
            vs.doctor,
            vs.right_eye,
            vs.left_eye,
            vs.r_eye,
            vs.l_eye,
            vs.history_of_eye_pain_and_discomfort,
            vs.difficult_in_vision_near_far,
            vs.history_of_redness_tching_in_eyes,
            vs.history_of_watering_or_discharge,
            vs.do_you_wear_glasses_or_contact_lenses,
            vs.history_of_chronic_diseases_dmhtn,
            vs.overall_vision_status,
            vs.diagnosis,
            vs.referral,
            vs.referral__comments
        FROM
            `tabVission Screening` vs
        WHERE
            {conditions}
        ORDER BY
            vs.date DESC, vs.full_name ASC
    """

    return frappe.db.sql(query, values, as_dict=True)


def build_conditions(filters):
    conditions = ["vs.docstatus < 2"]
    values = {}

    if filters.get("from_date"):
        conditions.append("vs.date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("vs.date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    if filters.get("doctor"):
        conditions.append("vs.doctor = %(doctor)s")
        values["doctor"] = filters["doctor"]

    if filters.get("overall_vision_status"):
        conditions.append("vs.overall_vision_status = %(overall_vision_status)s")
        values["overall_vision_status"] = filters["overall_vision_status"]

    if filters.get("diagnosis"):
        conditions.append("vs.diagnosis = %(diagnosis)s")
        values["diagnosis"] = filters["diagnosis"]

    if filters.get("sex"):
        conditions.append("vs.sex = %(sex)s")
        values["sex"] = filters["sex"]

    if filters.get("referral"):
        conditions.append("vs.referral = %(referral)s")
        values["referral"] = filters["referral"]

    return " AND ".join(conditions), values


# ─── Chart ───────────────────────────────────────────────────────────────────

def get_chart(data):
    normal_count = sum(1 for row in data if row.get("overall_vision_status") == "Normal")
    abnormal_count = sum(1 for row in data if row.get("overall_vision_status") == "Abnormal")
    not_recorded = len(data) - normal_count - abnormal_count

    return {
        "data": {
            "labels": ["Normal", "Abnormal", "Not Recorded"],
            "datasets": [
                {
                    "name": "Vision Status",
                    "values": [normal_count, abnormal_count, not_recorded],
                }
            ],
        },
        "type": "donut",
        "colors": ["#28a745", "#dc3545", "#6c757d"],
        "height": 280,
        "title": _("Vision Status Distribution"),
    }


# ─── Summary ─────────────────────────────────────────────────────────────────

def get_summary(data):
    total = len(data)
    normal = sum(1 for r in data if r.get("overall_vision_status") == "Normal")
    abnormal = sum(1 for r in data if r.get("overall_vision_status") == "Abnormal")
    wears_glasses = sum(1 for r in data if r.get("do_you_wear_glasses_or_contact_lenses") == "Yes")
    chronic_disease = sum(1 for r in data if r.get("history_of_chronic_diseases_dmhtn") == "Yes")
    referred_yes = sum(1 for r in data if r.get("referral") == "Yes")
    referred_no = sum(1 for r in data if r.get("referral") == "No")

    return [
        {
            "value": total,
            "label": _("Total Patients"),
            "datatype": "Int",
            "indicator": "blue",
        },
        {
            "value": normal,
            "label": _("Normal Vision"),
            "datatype": "Int",
            "indicator": "green",
        },
        {
            "value": abnormal,
            "label": _("Abnormal Vision"),
            "datatype": "Int",
            "indicator": "red",
        },
        {
            "value": wears_glasses,
            "label": _("Wear Glasses / Lenses"),
            "datatype": "Int",
            "indicator": "orange",
        },
        {
            "value": chronic_disease,
            "label": _("Chronic Disease (DM/HTN)"),
            "datatype": "Int",
            "indicator": "purple",
        },
        {
            "value": referred_yes,
            "label": _("Referred - Yes"),
            "datatype": "Int",
            "indicator": "red",
        },
        {
            "value": referred_no,
            "label": _("Referred - No"),
            "datatype": "Int",
            "indicator": "green",
        },
    ]