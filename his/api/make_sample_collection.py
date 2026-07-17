import frappe;
def make_sample_collection(doc, method=None , items = None):
    itms= []
    if items:
        itms = items
    else:   
        count=0
        for i in doc.items:
            if frappe.db.exists("Lab Test Template", i.item_code, cache=True):
            # if i.item_group == "Laboratory":
                count=count+1
                itms.append(
                            {
                            "lab_test": frappe.db.get_value("Lab Test Template", {"item":i.item_code},"name"),
                            "department": frappe.db.get_value("Lab Test Template", {"item":i.item_code},"department")

                            
                }
                )

    if itms and not doc.is_return:
        sm_doc = frappe.get_doc({
            'doctype': 'Sample Collection',
            'sample_qty': 1,
            'practitioner':doc.ref_practitioner,
            'patient': doc.patient,
            'lab_test': itms,
            'reff_invoice' : doc.name,
            'source_order' : doc.source_order,
            'doner' : doc.doner,
            # "for_patient" : doc.ref_patient,
            # "blood_donar" : 1
        })
        sm_doc.insert(ignore_permissions = True)
        sm_doc.lab_ref=sm_doc.name.split("-")[1]
        sm_doc.save()
        # if doc.ref_patient:
        #     blood_strore = 




    
@frappe.whitelist()
def token_numebr(doc, method=None):
    if not frappe.db.get_value('Sample Collection', doc.name, "name"):
        date = doc.date
        b = frappe.db.sql(f""" select Max(token_no) as max from `tabSample Collection` where date = '{date}'  ; """ , as_dict = True)
        num = b[0]['max'] 
        if num == None:
            num = 0
        doc.token_no = int(num) + 1
        # last_col = frappe.db.sql("""SELECT lab_ref FROM `tabSample Collection` ORDER BY creation DESC LIMIT 1 """, as_dict=True)
        # if last_col and last_col[0].get('lab_ref'):
        #     doc.lab_ref = int(last_col[0]['lab_ref']) + 1
        # # col = frappe.get_last_doc("Sample Collection")
        # # if col:
        # #     if col.lab_ref:
        # #         doc.lab_ref = int(col.lab_ref) + 1