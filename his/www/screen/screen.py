import frappe
from frappe.utils import today
current_date = today()

def get_context(context):
	return context


@frappe.whitelist(allow_guest=True)
def get_doctors():
    room = str(frappe.form_dict.get("room", ""))

    # Fetch doctors based on the given room number
    doctors = frappe.db.sql(
        """
        SELECT
            practitioner_name,
            doctor_room,
            department,
            break
        FROM `tabHealthcare Practitioner`
        WHERE doctor_room = %s
        """,
        (room,),
        as_dict=True,
    )

    # Fetch queue list filtered by room number
    # Patient stays in the list until status is "Closed" - being "Called" no longer removes them
    que = frappe.db.sql(
        """
        SELECT
            practitioner_name,
            token_no,
            patient_name,
            que_steps
        FROM `tabQue`
        WHERE
            date = CURDATE()
            AND status != "Closed"
            AND status != "Canceled"
            AND practitioner_name IN (
                SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = %s
            )
        ORDER BY token_no ASC
        """,
        (room,),
        as_dict=True,
    )

    # NOTE: "called" queue (by_doc_call) intentionally NOT queried here.
    # The list-style queue screen that calls get_doctors() only reads
    # `que` and `doctors` from the response - `by_doc_call` was fetched
    # but never rendered, so the query was pure overhead. If a future
    # screen needs it, add it back using the same pattern as get_drshiine().

    return {
        "que": que,
        "doctors": doctors,
        "by_doc_call": {},
    }


@frappe.whitelist(allow_guest=True)
def get_drshiine():
    room = str(frappe.form_dict.get("room", ""))
    if room == "drshiine":
        room = 1

    # Fetch doctors based on the given room number
    doctors = frappe.db.sql(
        """
        SELECT
            practitioner_name,
            doctor_room,
            department,
            break
        FROM `tabHealthcare Practitioner`
        WHERE doctor_room = %s
        """,
        (room,),
        as_dict=True,
    )

    # Fetch queue list filtered by room number
    # Patient stays in the list until status is "Closed" - being "Called" no longer removes them
    que = frappe.db.sql(
        """
        SELECT
            practitioner_name,
            token_no,
            patient_name,
            que_steps
        FROM `tabQue`
        WHERE
            date = CURDATE()
            AND status != "Closed"
            AND status != "Canceled"
            AND practitioner_name IN (
                SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = %s
            )
        ORDER BY token_no ASC
        """,
        (room,),
        as_dict=True,
    )

    # Fetch the called queue list (patients already called by the doctor)
    c_que = frappe.db.sql(
        """
        SELECT
            practitioner_name,
            from_no,
            to_no,
            date
        FROM `tabQue Managment`
        WHERE
            date = CURDATE()
            AND practitioner_name IN (
                SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = %s
            )
        ORDER BY modified ASC
        """,
        (room,),
        as_dict=True,
    )

    # Structure called queue by doctor
    by_doc_call = {item["practitioner_name"]: item for item in c_que}

    return {
        "que": que,
        "doctors": doctors,
        "by_doc_call": by_doc_call,
    }


@frappe.whitelist()
def get_collection():
    que = frappe.db.sql(
        """
        SELECT
            token_no,
            patient_name,
            que_steps
        FROM `tabSample Collection`
        WHERE date = CURDATE() AND que_steps != "Called"
        ORDER BY modified DESC
        """,
        as_dict=True,
    )

    c_que = frappe.db.sql(
        """
        SELECT
            token_no,
            patient_name,
            que_steps
        FROM `tabSample Collection`
        WHERE date = CURDATE() AND que_steps = "Called"
        ORDER BY modified DESC
        """,
        as_dict=True,
    )

    return que, c_que


@frappe.whitelist()
def is_user_logged_in(user):
	pass


def on_queue_change(doc, method=None):
    """
    Hooked to Que and Que Managment doc_events (see hooks.py).
    Fires whenever a patient is added, called, or closed, and pushes a
    lightweight realtime event so screens can refresh instantly instead
    of waiting on a poll interval.

    Kept deliberately tiny: we do NOT send the queue data itself here,
    just a signal + which doctor_room changed. The screen re-fetches via
    the existing REST endpoint on receipt, so there's only one place
    (get_doctors / get_drshiine) that builds the actual payload.
    """
    doctor_room = None
    practitioner_name = getattr(doc, "practitioner_name", None)

    if practitioner_name:
        doctor_room = frappe.db.get_value(
            "Healthcare Practitioner",
            {"practitioner_name": practitioner_name},
            "doctor_room",
        )

    frappe.publish_realtime(
        event="queue_update",
        message={
            "doctor_room": doctor_room,
            "doctype": doc.doctype,
        },
        after_commit=True,
    )