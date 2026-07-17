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
    query_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status = "Open"  
        AND que_steps != "Called"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY modified ASC
    """
    que = frappe.db.sql(query_que, as_dict=True)

    # Fetch the called queue list (patients already called by the doctor)
    called_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status = "Open"  
        AND que_steps = "Called"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY modified ASC
    """
    c_que = frappe.db.sql(called_que, as_dict=True)

    all_doc_query_ques = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,  -- ✅ Now correctly fetched from `tabHealthcare Practitioner`
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name  -- ✅ Join to get `doctor_room`
        WHERE 
            q.date = CURDATE()
            AND q.status = "Open"  
            AND q.que_steps != "Called"
        ORDER BY q.modified DESC
    """

    ques = frappe.db.sql(all_doc_query_ques, as_dict=True)

    all_doc_called_que = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,  -- ✅ Now correctly fetched from `tabHealthcare Practitioner`
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name  -- ✅ Join to get `doctor_room`
        WHERE 
            q.date = CURDATE()
            AND q.status = "Open"  
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
    query_que = f"""
    SELECT 
        practitioner_name,
        token_no,
        patient_name,
        que_steps
    FROM `tabQue`
    WHERE 
        date = CURDATE()
        AND status = "Open"  
        AND que_steps != "Called"
        AND practitioner_name IN (
            SELECT practitioner_name FROM `tabHealthcare Practitioner` WHERE doctor_room = '{room}'
        )
    ORDER BY modified ASC
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
            hp.doctor_room,  -- ✅ Now correctly fetched from `tabHealthcare Practitioner`
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name  -- ✅ Join to get `doctor_room`
        WHERE 
            q.date = CURDATE()
            AND q.status = "Open"  
            AND q.que_steps != "Called"
        ORDER BY q.modified DESC
    """

    ques = frappe.db.sql(all_doc_query_ques, as_dict=True)

    all_doc_called_que = """
        SELECT 
            q.practitioner_name,
            hp.doctor_room,  -- ✅ Now correctly fetched from `tabHealthcare Practitioner`
            q.token_no,
            q.patient_name,
            q.que_steps
        FROM `tabQue` q
        LEFT JOIN `tabHealthcare Practitioner` hp 
            ON q.practitioner_name = hp.practitioner_name  -- ✅ Join to get `doctor_room`
        WHERE 
            q.date = CURDATE()
            AND q.status = "Open"  
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
	# session = frappe.db.get_value("tabSessions", filters={"user": "cashier@testdomain.com"}, fieldname=["sessiondata", "session_expiry"])
	# if session and session[0] and session[1] and session[1] > frappe.utils.now():
	# 	print("Session of user cashier@testdomain.com is active")
	# else:
	# 	print("Session of user cashier@testdomain.com is not active")

	# 	return session
	
# 	sessions = frappe.db.sql(f"""SELECT * FROM `tabSessions` WHERE  user = "{user}" and lastupdate < DATE_SUB(NOW(), INTERVAL 1
# MINUTE)""", as_dict=True)
	# return session
