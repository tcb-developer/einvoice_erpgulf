# Copyright (c) 2026, erpgulf.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class EInvoiceTransactionType(Document):
	def validate(self):
		if not 1 <= (self.position or 0) <= 8:
			frappe.throw(_("Position must be between 1 and 8."))
		self.code = "".join("1" if i == self.position else "0" for i in range(1, 9))
