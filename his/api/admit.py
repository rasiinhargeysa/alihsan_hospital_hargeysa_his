from healthcare.healthcare.doctype.inpatient_record.inpatient_record import admit_patient
import frappe
import json 
from frappe import _



def admit_patient(inpatient_record, service_unit, check_in, inpatient_type, expected_discharge=None):
    # validate_nursing_tasks(inpatient_record)

    inpatient_record.admitted_datetime = check_in
    inpatient_record.status = "Admitted"
    inpatient_record.expected_discharge = expected_discharge

    inpatient_record.set("inpatient_occupancies", [])
    transfer_patient(inpatient_record, service_unit, check_in, inpatient_type)

    frappe.db.set_value(
        "Patient",
        inpatient_record.patient,
        {"inpatient_status": "Admitted", "inpatient_record": inpatient_record.name},
    )


def transfer_patient(inpatient_record, service_unit, check_in, inpatient_type):
    item_line = inpatient_record.append("inpatient_occupancies", {})
    item_line.service_unit = service_unit
    item_line.check_in = check_in
    item_line.invoiced = 1
    item_line.inpatient_type = inpatient_type

    inpatient_record.save(ignore_permissions=True)

    frappe.db.set_value("Healthcare Service Unit", service_unit, "occupancy_status", "Occupied")

@frappe.whitelist()
def admit_p(inp_doc, service_unit, amount=0, discount=0, paid_amount=0, patient=None,  is_insurance=0, insurance=None, practitioner=None, comment= None, expected_discharge=None, qofka=None,partner_company=None,):
    discount = float(discount) or 0
    paid_amount = float(paid_amount) or 0
    amount =  float(amount) or 0
    ip_doc = frappe.get_doc("Inpatient Record", inp_doc)
    patient_doc = frappe.get_doc("Patient", ip_doc.patient)
    patient_id = patient_doc.name

    frappe.db.set_value('Healthcare Service Unit', service_unit, 'patient', patient_id)

    ip_doc.bed = service_unit
    # ip_doc.insurance = insurance
    ip_doc.room = frappe.db.get_value("Healthcare Service Unit", service_unit, "service_unit_type")
    room_item = frappe.get_doc("Healthcare Service Unit Type", ip_doc.room)
    ip_doc.inpatient_status = "Admitted"

    ip_doc.save()
   
    check_in = frappe.utils.now()

    admit_patient(ip_doc, service_unit, check_in, expected_discharge)

    doc_plan = frappe.get_doc({
        "doctype": "Doctor Plan",
        "patient": ip_doc.patient,
        "ref_practitioner": ip_doc.admission_practitioner,
        "date": frappe.utils.getdate(),
        "room": frappe.db.get_value("Healthcare Service Unit", service_unit, "service_unit_type"),
        "bed": service_unit
    })

    doc_plan.insert(ignore_permissions=True)

    pos_profile = frappe.db.get_value("POS Profile User", {"user": frappe.session.user}, "parent")
    if not pos_profile:
        frappe.throw(_("No POS Profile found for the current user."))

    mode_of_payment = frappe.db.get_value("POS Payment Method", {"parent": pos_profile, "default": 1}, "mode_of_payment")
    if not mode_of_payment:
        frappe.throw(_("No default mode of payment found in the POS Profile."))
    if discount > room_item.rate:
        frappe.throw(_("Discount cannot exceed the room rate."))
    
    
    if insurance:
        
        sales_invoice_doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "patient": ip_doc.patient,
            "customer": patient_doc.customer,
            "ref_practitioner": ip_doc.admission_practitioner,
            "is_pos": 0,
            "so_type": "Cashiers",
            "is_insurance": 1,
            "insurance": insurance,
            "items": [{
                "item_code": room_item.item_code,
                "description" : service_unit,
                "bed" : service_unit,
                "uom": room_item.uom,
                "rate": room_item.rate,
                "qty": 1,
                "bed": service_unit
            }],
            "discount_amount": discount,
            "comment":comment
            
            
        })

        sales_invoice_doc.insert(ignore_permissions=True)
        sales_invoice_doc.submit()
        # return sales_invoice_doc.name
    if not insurance:
        # frappe.errprint("Else Part")
        sales_invoice_doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "patient": ip_doc.patient,
            "customer": patient_doc.customer,
            "ref_practitioner": ip_doc.admission_practitioner,
            "is_pos": 1,
            "so_type": "Cashiers",
            "pos_profile": pos_profile,
            "payments": [{
                "mode_of_payment": mode_of_payment,
                "amount": paid_amount
            }],
            "items": [{
                "item_code": room_item.item_code,
                "description" : service_unit,
                "bed" : service_unit,
                "uom": room_item.uom,
                "rate": room_item.rate,
                "qty": 1,
                # "bed": service_unit
            }],
            "discount_amount": discount,
               "comment":comment
        })

        sales_invoice_doc.insert(ignore_permissions=True)
        sales_invoice_doc.submit()
        
        # return sales_invoice_doc.name

    return sales_invoice_doc.name 
    
    # if frappe.db.get_value("Healthcare Service Unit", service_unit, "service_unit_type") == "ICU":
    #     frappe.get_doc({
    #         "doctype": "ICU",
    #         "patient": ip_doc.patient,
    #         "practitioner": ip_doc.primary_practitioner,
    #     }).insert(ignore_permissions=True)

    # customer = frappe.get_doc("Customer", frappe.db.get_value("Patient", ip_doc.patient, "customer"))
    # customer.allow_credit = 1
    # for row in customer.credit_limits:
    #     row.credit_limit = 25000
    # # customer.save()

    # Return the Sales Invoice name
    
    
@frappe.whitelist()
def invoice_addition_beds(doc , method = None):
    return
    # frappe.msgprint("Ok")
    if frappe.db.exists("Inpatient Record" , doc.name):
        for i in doc.inpatient_occupancies:
            if not i.invoiced:
                ip = doc
                patientinfo = frappe.get_doc("Patient" , ip.patient)
    
                service_unit_type = frappe.get_doc("Healthcare Service Unit Type", frappe.db.get_value("Healthcare Service Unit", i.service_unit, "service_unit_type"))
                
                patient = ip.patient
                patient_name = ip.patient_name
                customer = patientinfo.customer
                item_code = service_unit_type.item
                rate = float(service_unit_type.rate)
                desc = service_unit_type.description
                remark = i.service_unit
                practitioner = ip.primary_practitioner
                medical_department = ip.medical_department
                salesdoc = frappe.get_doc({
            
                            "patient": patient,
                            "patient_name": patient_name,
                            "customer" : customer,
                            "is_pos" : 0,
                            "so_type": "Cashiers",
                            "source_order" : "IPD",
                            "posting_date" : frappe.utils.getdate(),
                
                            'due_date' : frappe.utils.getdate(),
                        
                            "remarks" : remark,
                        
                        
                            "doctype": "Sales Invoice",
                            "cost_center":  "Main - DASSH",
                    
                            "ref_practitioner" : practitioner,
                            
                            "items": [
                                {
                                "item_code": item_code,
                                    "item_name": item_code,
                                    "description": desc,
                                
                
                                    "qty": 1,
                
                                    "rate": rate/2,
                                    "amount": 1*rate,
                
                
                
                                
                
                
                    
                
                                    "doctype": "Sales Invoice Item",
                
                                }
                            ],
                
                        })
                try:
                    salesdoc.insert()
                    salesdoc.submit()
                    frappe.db.set_value("Inpatient Occupancy" ,i.name ,  "invoiced" , 1)
                except:
                    pass
                    
            
            