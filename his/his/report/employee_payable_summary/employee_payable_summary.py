# Copyright (c) 2026, Al-Ihsan Hospital and contributors
# For license information, please see license.txt

"""Employee Balance Summary.

Visible columns:
    Employee ID | Employee Name | Mobile | Balance | Statement

The report reads balances directly from GL Entry by Employee party. It does not
filter by Account.account_type, so accounts whose Account Type is blank remain
included.

Signed balance convention:
    balance > 0  -> debit balance; employee owes the company
    balance < 0  -> credit balance; company owes the employee
"""

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0")
SQL_ZERO_TOLERANCE = Decimal("0.000000001")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    _validate_filters(filters)

    columns = get_columns()
    data = get_data(filters)

    # columns, data, message, chart, report_summary, skip_total_rows
    return columns, data, None, None, None, 1


def _validate_filters(filters):
    if not filters.get("company"):
        frappe.throw(_("Company is required."))

    filters.report_date = getdate(
        filters.get("report_date") or nowdate()
    )

    if filters.get("statement_from_date"):
        filters.statement_from_date = getdate(
            filters.statement_from_date
        )

        if filters.statement_from_date > filters.report_date:
            frappe.throw(
                _("Statement From Date cannot be after Report Date.")
            )


def _to_decimal(value):
    if value in (None, ""):
        return ZERO

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _round_money(value):
    """Round final employee balance to exactly 2 decimal places."""

    amount = _to_decimal(value).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )

    # Prevent -0.00.
    if amount == ZERO:
        amount = ZERO

    return float(amount)


def _doctype_fields(doctype):
    if not frappe.db.exists("DocType", doctype):
        return set()

    meta = frappe.get_meta(doctype)

    return {
        "name",
        "creation",
        "modified",
        "owner",
        "modified_by",
        "docstatus",
        "idx",
    }.union(
        df.fieldname
        for df in meta.fields
        if df.fieldname
    )


def _employee_mobile_sql():
    """Resolve the available mobile field on Employee."""

    employee_fields = _doctype_fields("Employee")

    for fieldname in (
        "cell_number",
        "personal_mobile_no",
        "mobile_no",
    ):
        if fieldname in employee_fields:
            return "NULLIF(TRIM(emp.`{0}`), '')".format(
                fieldname
            )

    return "NULL"


def get_columns():
    return [
        {
            "label": _("Employee ID"),
            "fieldname": "employee",
            "fieldtype": "Link",
            "options": "Employee",
            "width": 170,
        },
        {
            "label": _("Employee Name"),
            "fieldname": "employee_name",
            "fieldtype": "Data",
            "width": 280,
        },
        {
            "label": _("Mobile"),
            "fieldname": "mobile_no",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": _("Balance"),
            "fieldname": "balance",
            "fieldtype": "Currency",
            "options": "currency",
            "precision": 2,
            "width": 170,
        },
        {
            "label": _("Statement"),
            "fieldname": "statement",
            "fieldtype": "Data",
            "width": 125,
        },
    ]


def get_data(filters):
    gl_fields = _doctype_fields("GL Entry")

    conditions = [
        "gle.company = %(company)s",
        "gle.posting_date <= %(report_date)s",
        "gle.party_type = 'Employee'",
        "gle.party IS NOT NULL",
        "gle.party != ''",
    ]

    if "is_cancelled" in gl_fields:
        conditions.append(
            "COALESCE(gle.is_cancelled, 0) = 0"
        )
    elif "docstatus" in gl_fields:
        conditions.append(
            "gle.docstatus = 1"
        )

    values = {
        "company": filters.company,
        "report_date": filters.report_date,
        "zero_tolerance": str(SQL_ZERO_TOLERANCE),
    }

    if filters.get("employee"):
        conditions.append(
            "gle.party = %(employee)s"
        )
        values["employee"] = filters.employee

    if filters.get("account"):
        conditions.append(
            "gle.account = %(account)s"
        )
        values["account"] = filters.account

    mobile_sql = _employee_mobile_sql()

    rows = frappe.db.sql(
        """
        SELECT
            gle.party AS employee,

            MAX(
                COALESCE(
                    NULLIF(
                        TRIM(emp.employee_name),
                        ''
                    ),
                    gle.party
                )
            ) AS employee_name,

            MAX(
                {mobile_sql}
            ) AS mobile_no,

            SUM(
                COALESCE(gle.debit, 0)
                - COALESCE(gle.credit, 0)
            ) AS balance

        FROM `tabGL Entry` gle

        LEFT JOIN `tabEmployee` emp
            ON emp.name = gle.party

        WHERE {where_clause}

        GROUP BY
            gle.party

        HAVING ABS(
            SUM(
                COALESCE(gle.debit, 0)
                - COALESCE(gle.credit, 0)
            )
        ) > %(zero_tolerance)s

        ORDER BY
            ABS(
                SUM(
                    COALESCE(gle.debit, 0)
                    - COALESCE(gle.credit, 0)
                )
            ) DESC,
            employee_name ASC
        """.format(
            mobile_sql=mobile_sql,
            where_clause=" AND ".join(
                conditions
            ),
        ),
        values,
        as_dict=True,
    )

    currency = (
        frappe.db.get_value(
            "Company",
            filters.company,
            "default_currency",
        )
        or frappe.defaults.get_global_default(
            "currency"
        )
        or ""
    )

    data = []

    for result in rows:
        party = result.get("employee")

        balance = _round_money(
            result.get("balance")
        )

        # Hide values that become 0.00.
        if not party or balance == 0.0:
            continue

        party_dict = frappe._dict(
            employee_name=(
                result.get("employee_name")
                or party
            ),
            mobile_no=(
                result.get("mobile_no")
                or ""
            ),
            balance=balance,

            # Used by the Currency formatter.
            # This is not a visible column.
            currency=currency,
        )

        row = frappe._dict()

        row.employee = party

        # Add employee values first.
        row.update(party_dict)

        # Safely prepare Employee ID for JavaScript.
        party_argument = escape(
            json.dumps(str(party)),
            quote=True,
        )

        # Add button after row.update so it is not overwritten.
        row.statement = f"""
            <button
                type="button"
                class="btn btn-primary btn-xs"
                onclick='statementemployee({party_argument})'
            >
                {_("Statements")}
            </button>
        """

        data.append(row)

    return data