"""Transaction Types of a UAE e-invoice (BTAE-02, Marmin profile_execution_id).

The code has eight places; each place is one flag, and a sale can carry
several flags at once (Marmin: Free Trade Zone + Deemed Supply = 11000000).
On the Sales Invoice they are picked in the "Transaction Types" field (a
multi-select of E-Invoice Transaction Type); nothing picked is a normal
sale, 00000000.
"""

import frappe

DOCTYPE = "E-Invoice Transaction Type"
TABLE_FIELD = "custom_einvoice_transaction_types"
CODE_FIELD = "custom_einvoice_transaction_type_code"

# (name as on the Marmin portal, place in the code, what it means)
TYPES = (
	("Free Trade Zone", 1, "Supply in or to a Free Zone (Designated Zone). Marmin also needs the beneficiary ID."),
	("Deemed Supply", 2, "No payment, but treated as a sale for VAT, e.g. free goods given away as gifts or promotions."),
	("Profit Margin Scheme", 3, "Second-hand goods where VAT is charged only on the margin (VAT Category N)."),
	("Summary Invoice", 4, "One invoice for several supplies to the same customer in a period. Marmin also needs the invoice period."),
	("Continuous Supply", 5, "Supply billed periodically, e.g. rent, AMC or subscription."),
	("Agent Billing", 6, "Billing as a disclosed agent on behalf of a principal. Marmin also needs the principal's ID."),
	("Supply Through E-commerce", 7, "Sale to a customer through an online platform. Marmin also needs the delivery location."),
	("Exports", 8, "Goods or services supplied outside the UAE. Marmin also needs the delivery location."),
)


def ensure_transaction_types():
	"""Create the eight Transaction Types if they are missing (after_migrate)."""
	if not frappe.db.table_exists(DOCTYPE):
		return
	for name, position, description in TYPES:
		if frappe.db.exists(DOCTYPE, name):
			continue
		frappe.get_doc(
			{"doctype": DOCTYPE, "transaction_type": name, "position": position, "description": description}
		).insert(ignore_permissions=True)


def selected_positions(doc):
	"""Places (1 to 8) picked in the Transaction Types field of a document."""
	rows = (doc.get(TABLE_FIELD) if hasattr(doc, "get") else getattr(doc, TABLE_FIELD, None)) or []
	names = {row.get("transaction_type") if isinstance(row, dict) else row.transaction_type for row in rows}
	names.discard(None)
	if not names:
		return set()
	return set(frappe.get_all(DOCTYPE, filters={"name": ["in", list(names)]}, pluck="position"))


def set_transaction_type_code(doc, method=None):
	"""Sales Invoice validate: store the code the e-invoice will carry, so the
	form shows it (the form also updates it live while types are picked)."""
	if not doc.meta.has_field(CODE_FIELD):
		return
	from einvoice_erpgulf.countries.ae.profile import transaction_type_flags

	doc.set(CODE_FIELD, transaction_type_flags(doc))



def _is_trn(value):
	value = (value or "").strip()
	return len(value) == 15 and value.isdigit() and value.startswith("1") and value.endswith("03")


def _address_problems(name, label, need_outside_uae):
	"""What the delivery address lacks for Marmin's delivery location."""
	if not name:
		return [frappe._("select a Shipping Address on the invoice ({0})").format(label)]
	a = frappe.db.get_value("Address", name, ["address_line1", "city", "country", "state", "emirate", "custom_governorate"], as_dict=True) or {}
	link = frappe.utils.get_link_to_form("Address", name)
	missing = [field for field, value in (("Address Line 1", a.get("address_line1")), ("City", a.get("city")), ("Country", a.get("country"))) if not value]
	in_uae = a.get("country") == "United Arab Emirates"
	if in_uae and not a.get("emirate"):
		missing.append("Emirate")
	if a.get("country") and not in_uae and not (a.get("state") or a.get("custom_governorate")):
		missing.append("State/Province/Area")
	problems = []
	if missing:
		problems.append(frappe._("fill {0} on Shipping Address {1} ({2})").format(", ".join(missing), link, label))
	if need_outside_uae and in_uae:
		problems.append(frappe._("Shipping Address {0} is in the UAE; an export needs the delivery address outside the UAE ({1})").format(link, label))
	return problems


def _line_vat_code(item, default_label):
	from einvoice_erpgulf.countries.ae.profile import vat_category_code

	label = default_label
	if item.get("item_tax_template"):
		label = frappe.db.get_value("Item Tax Template", item.item_tax_template, "custom_vat_category") or default_label
	return vat_category_code(label) if label else "S"


def validate_transaction_type_details(doc, method=None):
	"""Sales Invoice before_submit: each Transaction Type picked needs extra
	details on the e-invoice (Marmin rejects it otherwise). All problems are
	listed in one message; nothing is checked for a normal sale (00000000)."""
	from einvoice_erpgulf.countries.ae.profile import transaction_type_flags

	flags = transaction_type_flags(doc)
	if flags == "00000000":
		return
	if not frappe.db.get_value("Company", doc.company, "custom_einvoice_enabled"):
		return  # no e-invoice for this company, nothing goes to Marmin
	problems = []

	if flags[0] == "1":  # Free Trade Zone
		if doc.get("customer") and not (frappe.db.get_value("Customer", doc.customer, "custom_fz_beneficiary_id") or "").strip():
			problems.append(frappe._(
				"Free Trade Zone: enter the FZ Beneficiary ID on Customer {0} (the Free Zone entity's 10-digit TIN or 15-digit TRN)"
			).format(frappe.utils.get_link_to_form("Customer", doc.customer, doc.customer_name or doc.customer)))

	if flags[2] == "1":  # Profit Margin Scheme
		wrong = [str(item.idx) for item in doc.get("items") or [] if _line_vat_code(item, doc.get("custom_vat_category")) != "N"]
		if wrong:
			problems.append(frappe._(
				"Profit Margin Scheme: every item must have VAT Category \"N - Margin scheme\" (rows {0}). Set it in VAT Category or the row's Item Tax Template"
			).format(", ".join(wrong)))

	if flags[3] == "1":  # Summary Invoice
		if not doc.get("from_date") or not doc.get("to_date"):
			problems.append(frappe._("Summary Invoice: fill From Date and To Date (the period this invoice covers)"))
		elif frappe.utils.getdate(doc.from_date) > frappe.utils.getdate(doc.to_date):
			problems.append(frappe._("Summary Invoice: From Date must be on or before To Date"))

	if flags[5] == "1":  # Agent Billing
		if not _is_trn(doc.get("custom_principal_trn")):
			problems.append(frappe._(
				"Agent Billing: enter the principal's TRN in Principal TRN (15 digits, starting with 1 and ending with 03)"
			))

	if flags[6] == "1":  # E-commerce
		problems += [frappe._("Supply Through E-commerce: {0}").format(p) for p in _address_problems(doc.get("shipping_address_name"), frappe._("delivery location"), False)]

	if flags[7] == "1":  # Exports
		problems += [frappe._("Exports: {0}").format(p) for p in _address_problems(doc.get("shipping_address_name"), frappe._("delivery location"), True)]

	if problems:
		frappe.throw(
			frappe._("This invoice cannot be submitted yet. The Transaction Types selected ({0}) need these details on the e-invoice:").format(flags)
			+ "<ul>" + "".join(f"<li>{p}</li>" for p in problems) + "</ul>"
			+ frappe._("Fill them and submit again, or remove the Transaction Type if it does not apply."),
			title=frappe._("E-Invoice details missing"),
		)
