"""Extra details a Marmin e-invoice needs for its Transaction Types
(profile_execution_id). Added only for the flags that are set, so a normal
sale (00000000) keeps exactly the payload it had before:

- 1 Free Trade Zone: buyer_customer_party.id = Customer FZ Beneficiary ID
- 4 Summary Invoice: invoice_period from the invoice's From Date / To Date
- 6 Agent Billing: seller_supplier_party.id = Principal TRN
- 7 E-commerce, 8 Exports: delivery (Shipping Address + delivery date)

Profit Margin Scheme (3) needs VAT category N on the lines, which the lines
already carry. Deemed (2) and Continuous (5) need nothing. The Sales Invoice
submit check (transaction_types.validate_transaction_type_details) makes
sure these values are present before anything is sent.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from einvoice_erpgulf.core.codes import country_code_mapping
from einvoice_erpgulf.countries.ae.profile import emirate_code


def _clean(data):
	"""Drop empty values; trim spaces and stray commas the address forms leave."""
	cleaned = {}
	for key, value in data.items():
		if isinstance(value, str):
			value = value.strip().strip(",").strip()
		if value not in (None, ""):
			cleaned[key] = value
	return cleaned


def delivery_date(doc):
	"""Latest Delivery Note date of the invoice rows, else the posting date."""
	notes = {row.delivery_note for row in doc.get("items") or [] if row.get("delivery_note")}
	dates = frappe.get_all("Delivery Note", filters={"name": ["in", list(notes)]}, pluck="posting_date") if notes else []
	return str(max(dates) if dates else getdate(doc.posting_date))


def delivery(doc):
	name = doc.get("shipping_address_name")
	if not name:
		frappe.throw(_("Select a Shipping Address on {0}: Marmin needs the delivery location for this Transaction Type.").format(doc.name))
	address = frappe.get_doc("Address", name)
	country_code = country_code_mapping().get((address.country or "").lower(), "AE")
	if country_code == "AE":
		subentity = emirate_code(address.get("emirate"))
	else:
		subentity = address.get("state") or address.get("custom_governorate")
	return {
		"actual_delivery_date": delivery_date(doc),
		"delivery_location": {
			"address": _clean(
				{
					"street_name": address.address_line1,
					"additional_street_name": address.address_line2,
					"city_name": address.city,
					"postal_zone": address.pincode,
					"country_subentity": subentity,
					"country": address.country,
					"country_code": country_code,
				}
			)
		},
	}


def add_transaction_details(payload, doc):
	flags = payload.get("profile_execution_id") or "00000000"
	if flags == "00000000":
		return
	if flags[0] == "1":
		beneficiary = (frappe.db.get_value("Customer", doc.customer, "custom_fz_beneficiary_id") or "").strip()
		if beneficiary:
			payload["buyer_customer_party"] = {"id": beneficiary}
	if flags[3] == "1" and doc.get("from_date") and doc.get("to_date"):
		payload["invoice_period"] = {"start_date": str(getdate(doc.from_date)), "end_date": str(getdate(doc.to_date))}
	if flags[5] == "1" and (doc.get("custom_principal_trn") or "").strip():
		payload["seller_supplier_party"] = {"id": doc.custom_principal_trn.strip()}
	if flags[6] == "1" or flags[7] == "1":
		payload["delivery"] = delivery(doc)
