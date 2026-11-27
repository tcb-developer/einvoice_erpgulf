// E-invoice for a Credit Note Journal Entry (UAE). The section shows only on
// a Credit Note entry (not system generated) of a company with e-invoicing
// switched on. Sending happens on the server after submit; see
// countries/ae/providers/marmin/journal_entry.py.

const EINV_JE_METHOD = "einvoice_erpgulf.countries.ae.providers.marmin.journal_entry";
const EINV_JE_SECTIONS = ["custom_einvoice_section", "custom_einvoice_line_section"];

function einv_je_is_credit_note(frm) {
    return frm.doc.voucher_type === "Credit Note" && !cint(frm.doc.is_system_generated);
}

function einv_je_refresh(frm) {
    if (!frm.get_field("custom_einvoice_item")) {
        return;
    }
    einvoice_render_status_ribbon(frm);
    const apply = (enabled) => {
        const on = Boolean(enabled) && einv_je_is_credit_note(frm);
        frm.toggle_display(EINV_JE_SECTIONS, on);
        frm.toggle_reqd("custom_einvoice_item", on && frm.doc.docstatus === 0);
        // Read-only once submitted (Finance, 27 Nov 2026).
        const locked = frm.doc.docstatus !== 0;
        ["custom_einvoice_item", "custom_vat_category"].forEach((f) =>
            frm.set_df_property(f, "read_only", locked ? 1 : 0)
        );
        einv_je_buttons(frm, on);
        if (on) {
            einv_je_preview(frm);
        }
    };
    if (!frm.doc.company) {
        apply(false);
        return;
    }
    einvoice_ui_labels(frm.doc.company).then((labels) =>
        apply(labels.country === "AE" && labels.einvoice_enabled)
    );
}

function einv_je_buttons(frm, on) {
    const group = __("E-Invoice");
    frm.remove_custom_button(__("Create E-Invoice"), group);
    frm.remove_custom_button(__("Get Status"), group);
    frm.remove_custom_button(__("PDF"), group);
    if (!on || frm.doc.docstatus !== 1) {
        return;
    }
    if (!einvoice_has_document_id(frm.doc)) {
        if (frm.doc.custom_einvoice_status !== "Queued") {
            frm.add_custom_button(
                __("Create E-Invoice"),
                () => {
                    if (!frm.doc.custom_einvoice_item) {
                        frappe.msgprint(__("Select Item and save before creating the e-invoice."));
                        return;
                    }
                    frappe.call({
                        method: EINV_JE_METHOD + ".create_einvoice",
                        args: { name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Creating E-Invoice..."),
                        callback: () => frm.reload_doc(),
                        error: () => frm.reload_doc(),
                    });
                },
                group
            );
        }
        return;
    }
    const reporting = (frm.doc.custom_reporting_status || "").toLowerCase();
    if (!["reported", "delivered", "rejected"].includes(reporting)) {
        frm.add_custom_button(
            __("Get Status"),
            () =>
                frappe.call({
                    method: EINV_JE_METHOD + ".refresh_status",
                    args: { name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Checking E-Invoice Status..."),
                    callback: () => frm.reload_doc(),
                }),
            group
        );
    }
    if (reporting === "reported" && !frm.doc.custom_document_pdf) {
        frm.add_custom_button(
            __("PDF"),
            () =>
                frappe.call({
                    method: "einvoice_erpgulf.core.attach.get_document_pdf",
                    args: { doctype: frm.doctype, invoice_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Fetching E-Invoice PDF..."),
                    callback: () => frm.reload_doc(),
                }),
            group
        );
    }
}

function einv_je_preview(frm) {
    const field = frm.get_field("custom_einvoice_line_preview");
    if (!field) {
        return;
    }
    clearTimeout(frm.__einv_je_preview_timer);
    frm.__einv_je_preview_timer = setTimeout(() => {
        frappe
            .call({ method: EINV_JE_METHOD + ".get_preview", args: { doc: frm.doc } })
            .then((r) => field.$wrapper.html(einv_je_preview_html(r.message || {})));
    }, 300);
}

function einv_je_preview_html(p) {
    const esc = frappe.utils.escape_html;
    if (p.error) {
        return `<div class="text-muted small">${esc(p.error)}</div>`;
    }
    const money = (v) => format_currency(v || 0);
    const head = ["Item", "Customer", "Qty", "Rate", "VAT", "Total"]
        .map((h, i) => `<th class="${i > 1 ? "text-right" : ""}">${__(h)}</th>`)
        .join("");
    const item = p.item ? esc(p.item) : `<span class="text-muted">${__("Select Item")}</span>`;
    return (
        `<div class="small text-muted" style="margin-bottom:6px">${__("E-Invoice Line")} · ` +
        `${__("Reason")}: VD (${__("Volume Discount")}) · ${__("VAT Category")}: ${esc(p.vat_category || "")}</div>` +
        `<table class="table table-bordered table-sm" style="margin:0"><thead><tr>${head}</tr></thead><tbody><tr>` +
        `<td>${item}</td><td>${esc(p.customer || "")}${p.customer_name ? " · " + esc(p.customer_name) : ""}</td>` +
        `<td class="text-right">1</td><td class="text-right">${money(p.rate)}</td>` +
        `<td class="text-right">${money(p.vat)}</td><td class="text-right">${money(p.total)}</td>` +
        `</tr></tbody></table>`
    );
}

frappe.ui.form.on("Journal Entry", {
    setup(frm) {
        frm.set_query("custom_einvoice_item", () => ({
            filters: { is_stock_item: 0, disabled: 0, item_group: "Credit Note Services" },
        }));
        if (!window.einv_je_listener_added) {
            window.einv_je_listener_added = true;
            frappe.realtime.on("einvoice_send_finished", (data) => {
                const f = window.cur_frm;
                if (f && data && data.doctype === "Journal Entry" && f.doctype === "Journal Entry" &&
                    f.doc.name === data.name && !f.is_dirty()) {
                    f.reload_doc();
                }
            });
        }
    },
    refresh: einv_je_refresh,
    voucher_type: einv_je_refresh,
    company: einv_je_refresh,
    custom_einvoice_item: einv_je_preview,
    custom_vat_category: einv_je_preview,
});

frappe.ui.form.on("Journal Entry Account", {
    debit_in_account_currency: (frm) => einv_je_is_credit_note(frm) && einv_je_preview(frm),
    credit_in_account_currency: (frm) => einv_je_is_credit_note(frm) && einv_je_preview(frm),
    party: (frm) => einv_je_is_credit_note(frm) && einv_je_preview(frm),
    account: (frm) => einv_je_is_credit_note(frm) && einv_je_preview(frm),
    accounts_remove: (frm) => einv_je_is_credit_note(frm) && einv_je_preview(frm),
});
