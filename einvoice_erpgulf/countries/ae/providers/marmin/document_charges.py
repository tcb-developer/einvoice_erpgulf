"""Document level charges (IBT-099) of a Sales Invoice, for its Marmin e-invoice.

Freight and similar charges are entered in the Taxes table as their own row
(usually "Actual"), and the VAT on them as a VAT row: "On Previous Row Amount"
pointing at the charge row, or an "Actual" VAT row right after it. The item
lines do not carry these charges, so they go in the payload's "charges";
Marmin adds them after the line totals and works out their VAT from the
charge's tax category.

Only rows that add to the total are sent. Rows that reduce it (for example
Order Processing Fees, whose VAT is booked on the input VAT account) are not
sent: how they are reported is still a Finance decision (27 Nov 2026).

A VAT row is a row whose account has Account Type "Tax", or a blank Account
Type under a "Tax" group such as "2300 - Duties and Taxes" (Finance books VAT
on "Vat Output Tax Account" / "Vat Input Tax Account", which have no type).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

STANDARD_RATE = 5.0
FREIGHT_REASON_CODE = "FC"  # UN/ECE Rec. 20 "Freight service"


def vat_accounts(names):
    names = list({name for name in names if name})
    if not names:
        return set()
    rows = frappe.get_all(
        "Account",
        filters={"name": ["in", names]},
        fields=["name", "account_type", "parent_account"],
    )
    parents = list({row.parent_account for row in rows if row.parent_account})
    tax_groups = (
        set(frappe.get_all("Account", filters={"name": ["in", parents], "account_type": "Tax"}, pluck="name"))
        if parents
        else set()
    )
    return {
        row.name
        for row in rows
        if row.account_type == "Tax" or (not row.account_type and row.parent_account in tax_groups)
    }


def default_vat_rate(doc):
    """Rate for the item lines: the first VAT row on Net Total. When there is
    none, the first row of the Taxes table, as before."""
    taxes = doc.get("taxes") or []
    vat = vat_accounts([row.get("account_head") for row in taxes])
    for row in taxes:
        if row.get("account_head") in vat and row.get("charge_type") == "On Net Total":
            return row.rate
    return taxes[0].rate if taxes else 0


def _amount(row):
    value = row.get("tax_amount_after_discount_amount")
    return flt(value if value else row.get("tax_amount"))


def _vat_rate_on(row, taxes, vat, sign):
    """VAT rate charged on a charge row, or None when it has no VAT."""
    for tax in taxes:
        if (
            tax.get("account_head") in vat
            and tax.get("charge_type") == "On Previous Row Amount"
            and cint(tax.get("row_id")) == cint(row.idx)
            and flt(tax.rate) > 0
        ):
            return flt(tax.rate)
    following = next((tax for tax in taxes if cint(tax.idx) == cint(row.idx) + 1), None)
    if following and following.get("account_head") in vat and following.get("charge_type") == "Actual":
        charge, tax_amount = sign * _amount(row), sign * _amount(following)
        if charge > 0 and tax_amount > 0:
            return flt(tax_amount / charge * 100, 2)
    return None


def document_charges(doc, default_vat_code, default_exemption_label=None):
    """The "charges" list for the Marmin payload; empty when the invoice has
    no charge rows. A return keeps its amounts negative in ERPNext, Marmin
    wants them positive."""
    taxes = doc.get("taxes") or []
    if not taxes:
        return []
    sign = -1 if cint(doc.get("is_return")) else 1
    vat = vat_accounts([row.get("account_head") for row in taxes])
    charges = []
    for row in taxes:
        account = row.get("account_head")
        if not account or account in vat:
            continue
        amount = flt(sign * _amount(row), 2)
        if amount <= 0:
            continue
        reason = (row.get("description") or account).strip()
        rate = _vat_rate_on(row, taxes, vat, sign)
        if rate is not None:
            if abs(rate - STANDARD_RATE) > 0.1:
                frappe.throw(
                    _(
                        "Row {0} of the Taxes table ({1}) carries VAT of {2}% on it. "
                        "The e-invoice can only send a charge at 5% VAT."
                    ).format(row.idx, account, rate),
                    title=_("E-Invoice charge"),
                )
            tax_category = {"id": "S", "percent": STANDARD_RATE, "tax_scheme": "VAT"}
        elif default_vat_code == "S":
            frappe.throw(
                _(
                    "Row {0} of the Taxes table ({1}, {2}) is a charge without VAT on a standard "
                    "rated invoice. Add its VAT as a row with Type \"On Previous Row Amount\" and "
                    "Reference Row # {0}, so the e-invoice matches the invoice."
                ).format(row.idx, account, amount),
                title=_("E-Invoice charge"),
            )
        else:
            tax_category = {"id": default_vat_code, "percent": 0.0, "tax_scheme": "VAT"}
            if default_vat_code == "E":
                if not default_exemption_label:
                    frappe.throw(
                        _("Row {0} of the Taxes table ({1}) is an exempt charge, but the invoice has no VAT Exemption Reason Code.").format(row.idx, account),
                        title=_("E-Invoice charge"),
                    )
                code, _sep, description = default_exemption_label.partition(" - ")
                tax_category["tax_exemption_reason_code"] = code.strip()
                tax_category["tax_exemption_reason"] = (description or default_exemption_label).strip()
        charge = {"reason": reason, "amount": amount, "tax_category": tax_category}
        if "freight" in account.lower():
            charge["reason_code"] = FREIGHT_REASON_CODE
        charges.append(charge)
    return charges
