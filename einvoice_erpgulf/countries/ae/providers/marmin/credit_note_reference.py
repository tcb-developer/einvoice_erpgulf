"""The original invoice(s) a sales credit note corrects, for its e-invoice.

Marmin wants every credit note to name the invoice(s) it corrects in
``billing_reference`` (invoice number and date). A credit note made from a
Return Delivery Note has no Return Against set (ERPNext does not copy it),
so the invoice is found from the documents the credit note came from:

1. Return Against, when set.
2. The Return Delivery Note: each credit note line points at a return DN
   line, which points at the original DN line; the invoice that billed that
   DN line is the original invoice.
3. The Sales Return Request the credit note was made for: the invoices
   picked on the request.
4. The Sales Order line: the invoice that billed it, only where exactly one
   invoice did.

Only submitted, non-return invoices of the same company and customer are
kept. Nothing here changes the credit note or the books: the result is used
only in the e-invoice payload.
"""

import frappe


def original_invoices(doc):
    """Original invoices of a return Sales Invoice, oldest first, as
    frappe._dict(name, posting_date). Empty list when none is found."""
    if doc.get("return_against"):
        names = [doc.return_against]
    else:
        names = (
            _from_return_delivery_notes(doc)
            or _from_sales_return_request(doc)
            or _from_sales_orders(doc)
        )
    return _valid_invoices(doc, names)


GOODS_RETURNED_REASON = "DL8.61.1.D"


def goods_returned(doc):
    """True when the credit note records goods coming back: made from a
    Return Delivery Note or a Sales Return Request, or updating stock itself."""
    return bool(
        doc.get("update_stock")
        or doc.get("custom_sales_return_id")
        or any(row.get("dn_detail") for row in doc.get("items") or [])
    )


def _row_values(doc, fieldname):
    return sorted({row.get(fieldname) for row in doc.get("items") or [] if row.get(fieldname)})


def _from_return_delivery_notes(doc):
    rows = _row_values(doc, "dn_detail")
    if not rows:
        return []
    return frappe.db.sql_list(
        """
        select distinct oi.parent
        from `tabDelivery Note Item` rdi
        join `tabSales Invoice Item` oi on oi.dn_detail = rdi.dn_detail and oi.docstatus = 1
        where rdi.name in %(rows)s and ifnull(rdi.dn_detail, '') != ''
        """,
        {"rows": rows},
    )


def _from_sales_return_request(doc):
    request = doc.get("custom_sales_return_id")
    if not request or not frappe.db.exists("DocType", "Sales Return Request"):
        return []
    names = frappe.get_all(
        "Sales Return Request Item",
        filters={
            "parent": request,
            "parenttype": "Sales Return Request",
            "reference_voucher_type": "Sales Invoice",
            "reference_voucher": ["is", "set"],
        },
        pluck="reference_voucher",
    )
    header = frappe.db.get_value("Sales Return Request", request, ["voucher_type", "voucher"], as_dict=True)
    if header and header.voucher_type == "Sales Invoice" and header.voucher:
        names.append(header.voucher)
    return sorted(set(names))


def _from_sales_orders(doc):
    rows = _row_values(doc, "so_detail")
    if not rows:
        return []
    billed = frappe.db.sql(
        """
        select oi.so_detail, oi.parent
        from `tabSales Invoice Item` oi
        join `tabSales Invoice` si on si.name = oi.parent
        where oi.so_detail in %(rows)s and oi.docstatus = 1 and si.is_return = 0
        """,
        {"rows": rows},
        as_dict=True,
    )
    by_line = {}
    for row in billed:
        by_line.setdefault(row.so_detail, set()).add(row.parent)
    # A Sales Order line billed by several invoices is ambiguous: skip it.
    return sorted({next(iter(p)) for p in by_line.values() if len(p) == 1})


def _valid_invoices(doc, names):
    names = [n for n in names if n and n != doc.name]
    if not names:
        return []
    return frappe.get_all(
        "Sales Invoice",
        filters={
            "name": ["in", names],
            "docstatus": 1,
            "is_return": 0,
            "company": doc.company,
            "customer": doc.customer,
        },
        fields=["name", "posting_date"],
        order_by="posting_date asc, name asc",
    )
