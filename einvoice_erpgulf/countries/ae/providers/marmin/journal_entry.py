"""E-invoice for a Credit Note Journal Entry (UAE, Marmin).

Finance posts rebates, price support and similar credits to customers as a
Journal Entry with Entry Type "Credit Note". It has no items and no original
invoice, so it is sent to Marmin as a stand-alone sales credit note:

* one line: the service item chosen in "Select Item", quantity 1, rate =
  the entry value (the customer's amount, less any VAT line, so VAT is not
  added twice);
* reason VD (volume discount), the one reason Marmin accepts without
  billing_reference, and no reference invoice;
* sent under the Journal Entry's own number.

Nothing here posts to the books: the Journal Entry already did. The credit
note is built as an unsaved Sales Invoice so the Marmin adapter's existing
credit note code (party, lines, VAT, type code) is reused as is.

The e-invoice is sent in the background after submit. If it fails, the
"Create E-Invoice" button sends it again. Submit itself is never blocked,
so Journal Entries created by code (for example from a CN Issue Request)
still submit; without an item they wait with the E-Invoice Pending ribbon
until Finance selects one and creates the e-invoice.
"""

import json

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_default_address
from frappe.utils import cint, flt

DOCTYPE = "Journal Entry"
ITEM_GROUP = "Credit Note Services"
REASON_VOLUME_DISCOUNT = "VD-Void"  # option label of custom_credit_note_reason_code; code "VD"
VAT_STANDARD = "S - Standard rated"
VAT_NOT_SUBJECT = "O - Not subject to VAT"

STATUS_QUEUED = "Queued"
STATUS_SUCCESS = "Success"
STATUS_FAILED = "Not Submitted"

SEND_QUEUE = "short"
SEND_JOB_TIMEOUT = 300
LOCK_TIMEOUT = 300


# ------------------------------------------------------------- eligibility
def _has_fields(doc):
    return bool(doc.meta.get_field("custom_einvoice_item"))


def is_credit_note_entry(doc):
    return doc.get("voucher_type") == "Credit Note" and not cint(doc.get("is_system_generated"))


def einvoicing_enabled(company):
    return bool(company and frappe.db.get_value("Company", company, "custom_einvoice_enabled"))


def applies(doc):
    """Credit Note entry, on a site with the fields, for a company with
    e-invoicing switched on."""
    return _has_fields(doc) and is_credit_note_entry(doc) and einvoicing_enabled(doc.company)


def has_document_id(doc):
    value = (doc.get("custom_document_id") or "").strip()
    return bool(value) and value.upper() != "NOT SUBMITTED"


# ---------------------------------------------------------------- amounts
def _customers(doc):
    return {
        row.party
        for row in doc.get("accounts") or []
        if row.get("party_type") == "Customer" and row.get("party")
    }


def _customer_count_message(count):
    return _(
        "A Credit Note Journal Entry for e-invoicing needs exactly one customer "
        "in its Accounting Entries; {0} found."
    ).format(count)


def _single_customer(doc):
    """The entry's one customer, or None. Never shows a message: the form
    preview runs on every change, and a frappe.throw caught here would still
    leave its popup queued for the browser."""
    customers = _customers(doc)
    return next(iter(customers)) if len(customers) == 1 else None


def customer_of(doc):
    customers = _customers(doc)
    if len(customers) != 1:
        frappe.throw(_customer_count_message(len(customers)), title=_("E-Invoice"))
    return customers.pop()


def _tax_accounts(doc):
    """VAT accounts on the entry: Account Type "Tax", or a ledger directly
    under a "Tax" group such as "2300 - Duties and Taxes". Finance posts VAT
    to "Vat Output Tax Account - NCS", whose own Account Type is blank."""
    accounts = {row.account for row in doc.get("accounts") or [] if row.get("account")}
    if not accounts:
        return set()
    rows = frappe.get_all(
        "Account",
        filters={"name": ["in", list(accounts)]},
        fields=["name", "account_type", "parent_account"],
    )
    parents = {row.parent_account for row in rows if row.parent_account}
    tax_groups = set(
        frappe.get_all(
            "Account", filters={"name": ["in", list(parents)], "account_type": "Tax"}, pluck="name"
        )
    ) if parents else set()
    return {
        row.name
        for row in rows
        if row.account_type == "Tax" or (not row.account_type and row.parent_account in tax_groups)
    }


def amounts(doc, customer=None):
    """(customer amount, VAT amount, net amount) of the entry, all positive."""
    customer = customer or customer_of(doc)
    tax_accounts = _tax_accounts(doc)
    gross = abs(
        sum(
            flt(row.credit) - flt(row.debit)
            for row in doc.accounts
            if row.get("party_type") == "Customer" and row.get("party") == customer
        )
    )
    vat = abs(sum(flt(row.debit) - flt(row.credit) for row in doc.accounts if row.account in tax_accounts))
    return gross, vat, flt(gross - vat, 2)


def default_vat_category(doc):
    customer = _single_customer(doc)
    if not customer:
        return None
    _gross, vat, _net = amounts(doc, customer)
    return VAT_STANDARD if vat else VAT_NOT_SUBJECT


# ------------------------------------------------------------ doc events
def validate(doc, method=None):
    if not (_has_fields(doc) and is_credit_note_entry(doc)):
        return
    if not doc.get("custom_vat_category"):
        doc.custom_vat_category = default_vat_category(doc)
    _check_item(doc)
    # Shown once on save, not on every field change; saving is not blocked.
    customers = _customers(doc)
    if len(customers) != 1 and einvoicing_enabled(doc.company):
        frappe.msgprint(_customer_count_message(len(customers)), title=_("E-Invoice"), indicator="orange")


def _check_item(doc):
    item = doc.get("custom_einvoice_item")
    if not item:
        return
    row = frappe.db.get_value("Item", item, ["is_stock_item", "disabled", "item_group"], as_dict=True)
    if not row or cint(row.is_stock_item) or cint(row.disabled) or row.item_group != ITEM_GROUP:
        frappe.throw(
            _("Select Item must be an enabled service item of the item group {0}.").format(ITEM_GROUP),
            title=_("E-Invoice"),
        )


def before_update_after_submit(doc, method=None):
    """Select Item and VAT Category can be set after submit, until the
    e-invoice exists."""
    if not (_has_fields(doc) and is_credit_note_entry(doc)):
        return
    if has_document_id(doc):
        before = doc.get_doc_before_save()
        if before and (
            before.get("custom_einvoice_item") != doc.get("custom_einvoice_item")
            or before.get("custom_vat_category") != doc.get("custom_vat_category")
        ):
            frappe.throw(_("The e-invoice already exists, so Select Item and VAT Category cannot change."))
    if not doc.get("custom_vat_category"):
        doc.custom_vat_category = default_vat_category(doc)
    _check_item(doc)


def on_submit(doc, method=None):
    if not applies(doc):
        return
    if not doc.get("custom_einvoice_item"):
        doc.db_set("custom_einvoice_status", STATUS_FAILED, update_modified=False)
        frappe.msgprint(
            _("No item selected, so no e-invoice was created. Select Item, save, then click Create E-Invoice."),
            alert=True,
            indicator="orange",
        )
        return
    doc.db_set("custom_einvoice_status", STATUS_QUEUED, update_modified=False)
    frappe.enqueue(
        "einvoice_erpgulf.countries.ae.providers.marmin.journal_entry.send_job",
        queue=SEND_QUEUE,
        timeout=SEND_JOB_TIMEOUT,
        enqueue_after_commit=True,
        job_id=f"einvoice_je::{doc.name}",
        deduplicate=True,
        name=doc.name,
    )
    frappe.msgprint(
        _("The e-invoice is being created in the background. Its status will show on this entry shortly."),
        alert=True,
        indicator="blue",
    )


def before_cancel(doc, method=None):
    if not (_has_fields(doc) and is_credit_note_entry(doc)):
        return
    if doc.get("custom_einvoice_status") == STATUS_QUEUED or _locked(doc.name):
        frappe.throw(
            _("The e-invoice for this entry is being created right now. Wait a minute, reload and try again."),
            title=_("Cancellation not allowed"),
        )
    reporting = (doc.get("custom_reporting_status") or "").strip().lower()
    if has_document_id(doc) and reporting not in ("failed", "rejected"):
        frappe.throw(
            _(
                "This entry already has an e-invoice (Document ID {0}) and cannot be cancelled."
            ).format(doc.custom_document_id),
            title=_("Cancellation not allowed"),
        )


# ---------------------------------------------------------------- sending
def _lock_key(name):
    return f"einvoice_je_send_lock::{name}"


def _locked(name):
    return bool(frappe.cache.get_value(_lock_key(name)))


def send_job(name):
    send_now(name, interactive=False)


def send_now(name, interactive=False):
    key = _lock_key(name)
    if _locked(name):
        if interactive:
            frappe.throw(_("This e-invoice is already being created. Wait a minute, then reload."))
        return {"result": "locked"}
    frappe.cache.set_value(key, 1, expires_in_sec=LOCK_TIMEOUT)
    try:
        return _send(name, interactive)
    finally:
        frappe.cache.delete_value(key)
        frappe.publish_realtime(
            "einvoice_send_finished", {"doctype": DOCTYPE, "name": name}, after_commit=True
        )


def _send(name, interactive):
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.docstatus != 1 or not applies(doc):
        if doc.get("custom_einvoice_status") == STATUS_QUEUED:
            doc.db_set("custom_einvoice_status", STATUS_FAILED, update_modified=False)
            frappe.db.commit()  # nosemgrep: frappe-manual-commit
        return {"result": "skipped"}

    if has_document_id(doc):
        if interactive:
            frappe.msgprint(
                _("This entry already has an e-invoice (Document ID {0}).").format(doc.custom_document_id),
                title=_("E-Invoice"),
            )
        return {"result": "already_sent"}

    attempts = cint(doc.get("custom_einvoice_send_attempts")) + 1
    try:
        from einvoice_erpgulf.core.provider_settings import get_active_provider_settings
        from einvoice_erpgulf.core.registry import get_adapter

        settings = get_active_provider_settings(doc.company)
        adapter = get_adapter(settings)
        if not hasattr(adapter, "_submit_credit_note"):
            frappe.throw(_("The e-invoice provider of {0} cannot send credit notes.").format(doc.company))
        credit_note = build_credit_note(doc)
        # Keep the outgoing JSON on the Journal Entry, not on the unsaved credit note.
        base_save = adapter.save_outgoing_payload
        adapter.save_outgoing_payload = lambda _cn, payload: base_save(doc, payload)
        status_code, response_data = adapter.submit_invoice("Sales Invoice", credit_note)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), f"E-Invoice Journal Entry Error - {name}")
        _record_failure(doc, attempts, str(e))
        if interactive:
            frappe.msgprint(_("E-Invoice creation failed: {0}").format(str(e)), title=_("E-Invoice"), indicator="red")
        return {"result": "failed", "message": str(e)}

    response_text = _response_text(response_data)
    existing_id = adapter.existing_document_id_from_response(status_code, response_data)
    if existing_id:
        _record_success(doc, attempts, response_text, existing_id, None)
        return {"result": "already_at_provider", "document_id": existing_id}

    parsed = adapter.parse_submit_response(status_code, response_data)
    if parsed.get("success"):
        _record_success(doc, attempts, response_text, parsed.get("document_id"), parsed.get("reporting_status"))
        if interactive:
            frappe.msgprint(_("E-Invoice created."), title=_("E-Invoice"), indicator="green")
        return {"result": "sent", "document_id": parsed.get("document_id")}

    frappe.log_error(f"HTTP {status_code}\n\n{response_text}", f"E-Invoice Not Accepted - {name}")
    _record_failure(doc, attempts, response_text)
    if interactive:
        frappe.msgprint(
            _("Marmin did not accept the e-invoice: {0}").format(frappe.utils.escape_html(response_text[:1000])),
            title=_("E-Invoice"),
            indicator="red",
        )
    return {"result": "failed", "message": response_text}


def _response_text(response_data):
    if isinstance(response_data, (dict, list)):
        return json.dumps(response_data, default=str)
    return str(response_data or "")


def _record_failure(doc, attempts, response_text):
    doc.db_set(
        {
            "custom_einvoice_status": STATUS_FAILED,
            "custom_submit_response": response_text,
            "custom_einvoice_send_attempts": attempts,
        },
        update_modified=False,
    )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _record_success(doc, attempts, response_text, document_id, reporting_status):
    doc.db_set(
        {
            "custom_einvoice_status": STATUS_SUCCESS,
            "custom_submit_response": response_text,
            "custom_document_id": document_id,
            "custom_reporting_status": (reporting_status or "pending").lower(),
            "custom_einvoice_send_attempts": attempts,
        },
        update_modified=False,
    )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit


# ------------------------------------------------------ credit note body
def build_credit_note(doc):
    """Unsaved return Sales Invoice carrying what the Marmin credit note
    builder reads. Never inserted."""
    item_code = doc.get("custom_einvoice_item")
    if not item_code:
        frappe.throw(_("Select Item before creating the e-invoice."), title=_("E-Invoice"))
    _check_item(doc)

    customer = customer_of(doc)
    gross, vat, net = amounts(doc)
    if net <= 0:
        frappe.throw(_("The entry has no amount for the customer to send as an e-invoice."), title=_("E-Invoice"))

    vat_category = doc.get("custom_vat_category") or default_vat_category(doc)
    vat_rate = round(vat / net * 100, 2) if vat else 0
    if vat_category == VAT_STANDARD and not vat:
        vat_rate = 5.0  # chosen by the user on an entry without a VAT line

    item = frappe.get_cached_doc("Item", item_code)
    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")

    cn = frappe.new_doc("Sales Invoice")
    cn.name = doc.name
    cn.update(
        {
            "is_return": 1,
            "company": doc.company,
            "customer": customer,
            # Same address a Sales Invoice gets: the customer's primary
            # address, even when Customer Primary Address is empty.
            "customer_address": get_default_address("Customer", customer),
            "posting_date": doc.posting_date,
            "posting_time": frappe.utils.get_time(doc.creation).strftime("%H:%M:%S") if doc.creation else None,
            "due_date": doc.posting_date,
            "currency": company_currency,
            "custom_vat_category": vat_category,
            "custom_credit_note_reason_code": REASON_VOLUME_DISCOUNT,
            "remarks": doc.get("user_remark"),
        }
    )
    cn.set("items", [])
    cn.append(
        "items",
        {
            "item_code": item.name,
            "item_name": item.item_name,
            "description": item.item_name,
            "uom": item.stock_uom,
            "stock_uom": item.stock_uom,
            "conversion_factor": 1,
            "qty": -1,
            "rate": net,
            "net_rate": net,
            "amount": -net,
            "net_amount": -net,
        },
    )
    cn.set("taxes", [])
    cn.append("taxes", {"charge_type": "On Net Total", "rate": vat_rate, "description": "VAT"})
    return cn


def preview(doc):
    """What the e-invoice line will be, for the form."""
    customers = _customers(doc)
    if len(customers) != 1:
        return {"error": _customer_count_message(len(customers))}
    customer = next(iter(customers))
    gross, vat, net = amounts(doc, customer)
    category = doc.get("custom_vat_category") or default_vat_category(doc)
    return {
        "item": doc.get("custom_einvoice_item"),
        "customer": customer,
        "customer_name": frappe.db.get_value("Customer", customer, "customer_name"),
        "qty": 1,
        "rate": net,
        "vat": vat if vat else (round(net * 0.05, 2) if category == VAT_STANDARD else 0),
        "vat_category": category,
        "total": gross if vat else (round(net * 1.05, 2) if category == VAT_STANDARD else net),
    }


# ------------------------------------------------------- form endpoints
@frappe.whitelist()
def create_einvoice(name: str):
    """Create E-Invoice button."""
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("submit")
    if not applies(doc):
        frappe.throw(_("E-invoicing does not apply to this entry."))
    return send_now(name, interactive=True)


@frappe.whitelist()
def get_preview(doc: str | dict):
    doc = frappe.get_doc(json.loads(doc) if isinstance(doc, str) else doc)
    doc.check_permission("read")
    return preview(doc)


@frappe.whitelist()
def refresh_status(name: str):
    """Get Status button."""
    from einvoice_erpgulf.core.status_sync import refresh_invoice_status

    frappe.get_doc(DOCTYPE, name).check_permission("read")
    return refresh_invoice_status(DOCTYPE, name)


def ensure_item_group():
    """after_migrate: the item group that holds the credit note service items."""
    if not frappe.db.exists("DocType", "Item Group") or frappe.db.exists("Item Group", ITEM_GROUP):
        return
    parent = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name")
    frappe.get_doc(
        {"doctype": "Item Group", "item_group_name": ITEM_GROUP, "parent_item_group": parent, "is_group": 0}
    ).insert(ignore_permissions=True)
