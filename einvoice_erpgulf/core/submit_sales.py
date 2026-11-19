"""Sales Invoice submission to the company's ASP (on_submit hook, buttons, bulk)."""
import frappe
import json
import time
from frappe import _
from einvoice_erpgulf.core.provider_settings import get_active_provider_settings
from einvoice_erpgulf.core.registry import get_adapter
from einvoice_erpgulf.core.attach import get_document_xml
from einvoice_erpgulf.core.attach import get_document_pdf
from einvoice_erpgulf.core.validation import success_log

def send_invoice_to_provider(doc, method=None):
    """ On Sales Invoice Submit and after JSON generation """

    try:
        settings = get_active_provider_settings(doc.company)
        adapter = get_adapter(settings)

        status_code, response_data = adapter.submit_invoice("Sales Invoice", doc)

        if isinstance(response_data, (dict, list)):
            pretty_response = json.dumps(response_data, indent=2)
        else:
            pretty_response = str(response_data)

        html = """
            <p><b>HTTP Status:</b> {status_code}</p>
            <pre style="white-space:pre-wrap;background:#f6f8fa;padding:12px;
                border-radius:6px;max-height:400px;overflow:auto;font-size:12px;">{response}</pre>
            """.format(
                status_code=status_code,
                response=frappe.utils.escape_html(pretty_response),
            )


        frappe.msgprint(html, title="E-Invoice Response", wide=True)

        return status_code, response_data
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "E-Invoice Submit Error")
        
        frappe.throw(
            _("Error while sending invoice to the e-invoicing provider: {0}").format(str(e))
        )



from typing import Optional, Union
from frappe.model.document import Document
import requests
from frappe.utils import add_to_date, cint, now_datetime

# Reliable sending (Newcom fork)
# ------------------------------
# On submit the invoice is only queued. The send runs in a background job
# after the submit is committed, so a slow or failing provider never holds up
# or rolls back the submit, and an invoice the ERP did not save is never sent.
# Failures caused by the network or the provider (timeouts, 5xx, 429, ...) are
# retried automatically with growing waits. An invoice is never sent twice: a
# lock per invoice stops two sends running together, an invoice that already
# has a Document ID is skipped, and if the provider already has the invoice
# number (for example after a reply was lost) its existing Document ID is
# stored instead of creating a second e-invoice.

STATUS_FIELD = "custom_einvoice_status"
STATUS_QUEUED = "Queued"
STATUS_NOT_SUBMITTED = "Not Submitted"
STATUS_SUCCESS = "Success"

SEND_QUEUE = "default"
SEND_JOB_TIMEOUT = 900
SEND_LOCK_SECONDS = 600
MAX_SEND_ATTEMPTS = 6
# Wait before the next automatic try, after attempt 1, 2, 3, ...
RETRY_WAIT_MINUTES = (5, 15, 30, 60, 120)
# A queued send that has not run by then is queued again by the scheduler.
QUEUED_SAFETY_MINUTES = 10
RETRY_BATCH_SIZE = 20
# HTTP answers worth trying again; any other 4xx means the data must be fixed.
TRANSIENT_HTTP_STATUSES = (401, 408, 425, 429)


def _has_document_id(value):
    value = (value or "").strip()
    return bool(value) and value.upper() != "NOT SUBMITTED"


def _einvoicing_enabled(company):
    return bool(frappe.db.get_value("Company", company, "custom_einvoice_enabled"))


def _is_transient(status_code):
    return status_code is None or status_code >= 500 or status_code in TRANSIENT_HTTP_STATUSES


def _next_retry_after(attempts):
    """When to try again after this many failed attempts, or None to stop."""
    if attempts >= MAX_SEND_ATTEMPTS:
        return None
    wait = RETRY_WAIT_MINUTES[min(attempts, len(RETRY_WAIT_MINUTES)) - 1]
    return add_to_date(now_datetime(), minutes=wait)


def _send_job_id(invoice_name):
    return f"einvoice_send::{invoice_name}"


def _lock_name(invoice_name):
    return f"einvoice_send_lock::{invoice_name}"


def _lock_key(invoice_name):
    return frappe.cache.make_key(_lock_name(invoice_name))


def _acquire_send_lock(invoice_name):
    return bool(frappe.cache.set(_lock_key(invoice_name), 1, nx=True, ex=SEND_LOCK_SECONDS))


def _release_send_lock(invoice_name):
    frappe.cache.delete(_lock_key(invoice_name))


def is_send_in_progress(invoice_name):
    # exists() adds the site prefix itself, so it gets the plain name.
    return bool(frappe.cache.exists(_lock_name(invoice_name)))


def _response_text(response_data):
    if isinstance(response_data, (dict, list)):
        return json.dumps(response_data, indent=4)
    return str(response_data)


def _show_response_popup(status_code, response_data):
    html = """
        <p><b>HTTP Status:</b> {status_code}</p>
        <pre style="white-space:pre-wrap;background:#f6f8fa;padding:12px;
            border-radius:6px;max-height:400px;overflow:auto;font-size:12px;">{response}</pre>
        """.format(
        status_code=status_code,
        response=frappe.utils.escape_html(_response_text(response_data)),
    )
    frappe.msgprint(html, title="E-Invoice Response", wide=True)


def queue_einvoice_send(doc):
    """on_submit: mark the invoice Queued and send it after the commit."""
    doc.db_set(
        {
            STATUS_FIELD: STATUS_QUEUED,
            "custom_einvoice_next_retry": add_to_date(now_datetime(), minutes=QUEUED_SAFETY_MINUTES),
        },
        update_modified=False,
    )
    frappe.enqueue(
        "einvoice_erpgulf.core.submit_sales.send_einvoice_job",
        queue=SEND_QUEUE,
        timeout=SEND_JOB_TIMEOUT,
        enqueue_after_commit=True,
        job_id=_send_job_id(doc.name),
        deduplicate=True,
        invoice_name=doc.name,
    )
    frappe.msgprint(
        _("The e-invoice is being sent in the background. Its status will show on this invoice shortly."),
        alert=True,
        indicator="blue",
    )



def send_einvoice_job(invoice_name):
    """Background job: send one invoice, then tell an open form to reload."""
    result = send_einvoice_now(invoice_name, interactive=False)
    frappe.publish_realtime(
        "einvoice_send_finished",
        {"name": invoice_name, "result": (result or {}).get("result")},
        doctype="Sales Invoice",
        docname=invoice_name,
        after_commit=True,
    )
    return result


def send_einvoice_now(invoice_name, interactive=False):
    """Send one submitted Sales Invoice, at most once at a time."""
    if not _acquire_send_lock(invoice_name):
        if interactive:
            frappe.msgprint(
                _("This invoice is already being sent. Wait a minute, then reload the invoice."),
                title=_("E-Invoice"),
            )
        return {"result": "busy"}
    try:
        return _send_einvoice(invoice_name, interactive)
    finally:
        _release_send_lock(invoice_name)


def _record_failure(doc, attempts, transient, response_text):
    next_retry = _next_retry_after(attempts) if transient else None
    doc.db_set(
        {
            STATUS_FIELD: STATUS_NOT_SUBMITTED,
            "custom_submit_response": response_text,
            "custom_einvoice_send_attempts": attempts,
            "custom_einvoice_next_retry": next_retry,
        }
    )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit
    return {"result": "failed", "will_retry": bool(next_retry), "next_retry": next_retry}


def _record_success(doc, adapter, settings, attempts, status_code, response_text,
                    document_id, reporting_status, exchange_status):
    if not reporting_status:
        reporting_status = "pending"
    doc.db_set(
        {
            STATUS_FIELD: STATUS_SUCCESS,
            "custom_submit_response": response_text,
            "custom_reporting_status": reporting_status,
            "custom_document_id": document_id,
            "custom_einvoice_send_attempts": attempts,
            "custom_einvoice_next_retry": None,
        }
    )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit

    if status_code in (200, 201) and getattr(adapter, "AUTO_FETCH_DOCUMENTS_ON_SUBMIT", True):
        retry_attempts = max(1, getattr(adapter, "DOCUMENT_FETCH_RETRY_ATTEMPTS", 1))
        retry_delay_seconds = getattr(adapter, "DOCUMENT_FETCH_RETRY_DELAY_SECONDS", 0)
        for fetch_fn, file_label in (
            (get_document_xml, "XML"),
            (get_document_pdf, "PDF"),
        ):
            for attempt in range(1, retry_attempts + 1):
                message_log_length_before = len(frappe.local.message_log)
                try:
                    fetch_fn("Sales Invoice", doc.name)
                    break
                except Exception:
                    del frappe.local.message_log[message_log_length_before:]
                    if attempt == retry_attempts:
                        frappe.log_error(frappe.get_traceback(), f"E-Invoice {file_label} Fetch Error")
                    elif retry_delay_seconds:
                        time.sleep(retry_delay_seconds)

    success_log(
        title="E-Invoice Submitted Successfully",
        document_id=document_id,
        participant_id=settings.participant_id,
        invoice_number=doc.name,
        reporting_status=reporting_status,
        exchange_status=exchange_status,
        status=STATUS_SUCCESS,
        submit_response=response_text,
    )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit
    return {"result": "sent", "document_id": document_id}


def _send_einvoice(invoice_name, interactive):
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    if doc.docstatus != 1 or not _einvoicing_enabled(doc.company):
        values = {"custom_einvoice_next_retry": None}
        if doc.get(STATUS_FIELD) == STATUS_QUEUED:
            values[STATUS_FIELD] = STATUS_NOT_SUBMITTED  # never leave it stuck as Queued
        doc.db_set(values, update_modified=False)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit
        return {"result": "skipped"}

    if _has_document_id(doc.custom_document_id):
        if doc.get(STATUS_FIELD) != STATUS_SUCCESS or doc.get("custom_einvoice_next_retry"):
            doc.db_set({STATUS_FIELD: STATUS_SUCCESS, "custom_einvoice_next_retry": None})
            frappe.db.commit()  # nosemgrep: frappe-manual-commit
        if interactive:
            frappe.msgprint(
                _("This invoice already has an e-invoice (Document ID {0}). It is not sent again.").format(
                    doc.custom_document_id
                ),
                title=_("E-Invoice"),
            )
        return {"result": "already_sent", "document_id": doc.custom_document_id}

    attempts = cint(doc.get("custom_einvoice_send_attempts")) + 1
    try:
        settings = get_active_provider_settings(doc.company)
        adapter = get_adapter(settings)
        status_code, response_data = adapter.submit_invoice("Sales Invoice", doc)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "E-Invoice Submit Error")
        transient = isinstance(e, requests.exceptions.RequestException)
        result = _record_failure(doc, attempts, transient, str(e))
        if interactive:
            frappe.msgprint(_("E-Invoice processing failed: {0}").format(str(e)))
        return result

    if interactive:
        _show_response_popup(status_code, response_data)
    response_text = _response_text(response_data)

    # The provider already has this invoice number: keep its e-invoice.
    existing_id = adapter.existing_document_id_from_response(status_code, response_data)
    if existing_id:
        result = _record_success(doc, adapter, settings, attempts, status_code, response_text,
                                 existing_id, None, None)
        try:
            from einvoice_erpgulf.core.status_sync import refresh_invoice_status
            refresh_invoice_status("Sales Invoice", doc.name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), f"E-Invoice Status Sync Error - {doc.name}")
        result["result"] = "already_at_provider"
        return result

    parsed = adapter.parse_submit_response(status_code, response_data)
    if parsed.get("success"):
        return _record_success(doc, adapter, settings, attempts, status_code, response_text,
                               parsed.get("document_id"), parsed.get("reporting_status"),
                               parsed.get("exchange_status"))

    frappe.log_error(
        f"HTTP {status_code}\n\n{response_text}", f"E-Invoice Not Accepted - {doc.name}"
    )
    return _record_failure(doc, attempts, _is_transient(status_code), response_text)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def generate_and_send_einvoice(doc: Union[Document, str], method: Optional[str] = None):
    """on_submit hook: queue the send. Send Invoice button and bulk send:
    send now (the invoice is already saved)."""
    if isinstance(doc, str):
        doc = frappe.parse_json(doc)

    if isinstance(doc, dict):
        doc = frappe.get_doc(doc)
    if doc.doctype != "Sales Invoice":
        return
    if not _einvoicing_enabled(doc.company):
        return  # e-invoicing is switched off for this company

    if method == "on_submit":
        queue_einvoice_send(doc)
        return {"result": "queued"}

    frappe.has_permission("Sales Invoice", "read", doc=doc.name, throw=True)
    return send_einvoice_now(doc.name, interactive=True)


def retry_unsent_einvoices():
    """Scheduled every 5 minutes (hooks.py): queue the sends whose retry
    time has come (failed for a temporary reason, or queued but not run)."""
    # Query builder on purpose: get_all turns "<=" on a date into
    # IFNULL(field, '0001-01-01') <= now, which would match every invoice
    # with no retry time at all (and could not use the index).
    si = frappe.qb.DocType("Sales Invoice")
    rows = (
        frappe.qb.from_(si)
        .select(si.name, si.company, si.custom_document_id)
        .where(
            (si.docstatus == 1)
            & si.custom_einvoice_next_retry.isnotnull()
            & (si.custom_einvoice_next_retry <= now_datetime())
        )
        .orderby(si.custom_einvoice_next_retry)
        .limit(RETRY_BATCH_SIZE)
        .run(as_dict=True)
    )
    for row in rows:
        try:
            if _has_document_id(row.custom_document_id) or not _einvoicing_enabled(row.company):
                frappe.db.set_value("Sales Invoice", row.name, "custom_einvoice_next_retry", None,
                                    update_modified=False)
                continue
            # Push the safety time forward so the next run does not queue it again.
            frappe.db.set_value(
                "Sales Invoice", row.name, "custom_einvoice_next_retry",
                add_to_date(now_datetime(), minutes=QUEUED_SAFETY_MINUTES), update_modified=False,
            )
            frappe.enqueue(
                "einvoice_erpgulf.core.submit_sales.send_einvoice_job",
                queue=SEND_QUEUE,
                timeout=SEND_JOB_TIMEOUT,
                job_id=_send_job_id(row.name),
                deduplicate=True,
                invoice_name=row.name,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"E-Invoice Retry Error - {row.name}")
    frappe.db.commit()  # nosemgrep: frappe-manual-commit


@frappe.whitelist()
def bulk_send_invoices(invoices: list | str):
    """Bulk send multiple invoices to the provider, with error handling and status tracking."""
    if isinstance(invoices, str):
        invoices = frappe.parse_json(invoices)

    success = []
    skipped = []
    failed = []

    for invoice in invoices:
        try:
            # Load documents
            doc = frappe.get_doc("Sales Invoice", invoice)
            company_doc = frappe.get_doc("Company", doc.company)

            status = doc.custom_einvoice_status

            if status == "Success":
                skipped.append(invoice)
                continue

            if doc.docstatus == 0 and company_doc.custom_einvoice_enabled == 1:
                doc.submit()
                success.append(invoice)

            elif doc.docstatus == 1 and company_doc.custom_einvoice_enabled == 1:
                generate_and_send_einvoice(doc)
                success.append(invoice)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"E-Invoice Bulk Submission Error: {invoice}")
            failed.append(f"{invoice} : {str(e)}")

    return {
        "success": success,
        "skipped": skipped,
        "failed": failed
    }