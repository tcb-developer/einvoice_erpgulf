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
