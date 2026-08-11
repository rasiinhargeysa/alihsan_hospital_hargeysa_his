import frappe
from frappe.utils import today
current_date = today()
def get_context(context):
	return context

@frappe.whitelist(allow_guest=True)
def get_doctors():
    room = str(frappe.form_dict.get("room", ""))

    # Fetch doctors based on the given room number
    query = f"""
    SELECT 
        practitioner_name,
        doctor_room,
        department,
        break
    FROM `tabHealthcare Practitioner`
    WHERE doctor_room = '{room}'
    """

    doctors = frappe.db.sql(query, as_dict=True)

    # Fetch queue list filtered by room number
    # Patient stays in the list until status is "Closed" - being "Called" no longer removes them
    query_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status != "Closed"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY token_no ASC
    """
    que = frappe.db.sql(query_que, as_dict=True)

    # Fetch the called queue list (patients already called by the doctor, still open)
    called_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status != "Closed"
        AND que_steps = "Called"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY token_no ASC
    """
    c_que = frappe.db.sql(called_que, as_dict=True)

    all_doc_query_ques = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name
        WHERE 
            q.date = CURDATE()
            AND q.status != "Closed"
        ORDER BY q.modified DESC
    """

    ques = frappe.db.sql(all_doc_query_ques, as_dict=True)

    all_doc_called_que = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name
        WHERE 
            q.date = CURDATE()
            AND q.status != "Closed"
            AND q.que_steps = "Called"
        ORDER BY q.modified DESC
    """

    called_ques = frappe.db.sql(all_doc_called_que, as_dict=True)

    # Structure called queue by doctor
    by_doc_call = {item["practitioner_name"]: item for item in c_que}

    return {
        "que": que,
        "doctors": doctors,
        "by_doc_call": by_doc_call,
        "ques": ques,
        "called_ques": called_ques
    }

@frappe.whitelist(allow_guest=True)
def get_drshiine():
    room = str(frappe.form_dict.get("room", ""))
    frappe.errprint(room)
    if room == "drshiine":
        room = 1

    # Fetch doctors based on the given room number
    query = f"""
    SELECT 
        practitioner_name,
        doctor_room,
        department,
        break
    FROM `tabHealthcare Practitioner`
    WHERE doctor_room = '{room}'
    """

    doctors = frappe.db.sql(query, as_dict=True)

    # Fetch queue list filtered by room number
    # Patient stays in the list until status is "Closed" - being "Called" no longer removes them
    query_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status != "Closed"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY token_no ASC
    """
    que = frappe.db.sql(query_que, as_dict=True)

    # Fetch the called queue list (patients already called by the doctor)
    called_que = f"""
    SELECT 
        practitioner_name,
        from_no,
        to_no,
        date
    FROM `tabQue Managment`
    WHERE 
        date = CURDATE()
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY modified ASC
    """
    c_que = frappe.db.sql(called_que, as_dict=True)

    all_doc_query_ques = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name
        WHERE 
            q.date = CURDATE()
            AND q.status != "Closed"
        ORDER BY q.modified DESC
    """

    ques = frappe.db.sql(all_doc_query_ques, as_dict=True)

    all_doc_called_que = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name
        WHERE 
            q.date = CURDATE()
            AND q.status != "Closed"
            AND q.que_steps = "Called"
        ORDER BY q.modified DESC
    """

    called_ques = frappe.db.sql(all_doc_called_que, as_dict=True)

    # Structure called queue by doctor
    by_doc_call = {item["practitioner_name"]: item for item in c_que}

    return {
        "que": que,
        "doctors": doctors,
        "by_doc_call": by_doc_call,
        "ques": ques,
        "called_ques": called_ques
    }


@frappe.whitelist()
def get_collection():

	query_que = """SELECT 
	token_no,
	patient_name,

	
	que_steps
		FROM `tabSample Collection`
		where date = CURDATE()  and que_steps != "Called"
		
		order by  modified desc
		
		"""
	que =frappe.db.sql(query_que , as_dict = 1)
	

	
	called_que = """SELECT 
	
	token_no,
	patient_name,
	
	que_steps
		FROM `tabSample Collection`
		where date = CURDATE()  and que_steps = "Called"
		
		order by  modified desc
		
		"""
	c_que = frappe.db.sql(called_que , as_dict = 1)
	

	# cnx.close()


	return que , c_que


@frappe.whitelist()
def is_user_logged_in(user):
	pass