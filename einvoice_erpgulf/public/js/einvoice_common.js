// Shared UI helpers for Sales Invoice and Purchase Invoice forms.
//
// Labels that differ per country (the tax authority's short name, what the
// provider is called) come from the company's country profile through one
// whitelisted call, cached per company for the session.

window.einvoice_labels_cache = window.einvoice_labels_cache || {};

function einvoice_ui_labels(company) {
    const cache = window.einvoice_labels_cache;
    if (cache[company]) {
        return Promise.resolve(cache[company]);
    }
    return frappe
        .call({
            method: "einvoice_erpgulf.core.registry.get_ui_labels",
            args: { company: company },
        })
        .then((r) => {
            cache[company] = r.message || {};
            return cache[company];
        })
        .catch(() => ({}));
}

// The provider's Document ID. The field's default is the text "Not
// Submitted", so that value means no e-invoice exists yet.
function einvoice_has_document_id(doc) {
    const id = (doc.custom_document_id || "").trim();
    return Boolean(id) && id.toUpperCase() !== "NOT SUBMITTED";
}

// Oman-only invoice fields. They are installed for every company, so show
// them (and require the credit note reason on a return) only when the
// invoice's company is in Oman AND has e-invoicing switched on. Until Oman
// e-invoicing is set up, nobody sees or fills them.
const EINVOICE_OMAN_FIELDS = [
    "custom_om_transaction_type_code",
    "custom_om_credit_note_reason_code",
    "custom_om_vat_exemption_reason_code",
    "custom_om_zero_rating_reason_code",
];

function einvoice_toggle_oman_fields(frm) {
    const fields = EINVOICE_OMAN_FIELDS.filter((f) => frm.get_field(f));
    if (!fields.length) {
        return;
    }
    const apply = (is_oman) => {
        frm.toggle_display(fields, is_oman);
        frm.toggle_reqd("custom_om_credit_note_reason_code", Boolean(is_oman && frm.doc.is_return));
    };
    if (!frm.doc.company) {
        apply(false);
        return;
    }
    einvoice_ui_labels(frm.doc.company).then((labels) =>
        apply(labels.country === "OM" && Boolean(labels.einvoice_enabled))
    );
}

// Status corner ribbon, drawn into the HTML field
// custom_einvoice_status_notification. A CSS ribbon: identical size and
// position for every status, right-aligned in its own column and clipped to
// a fixed box so it never overlaps the sidebar.
function einvoice_render_status_ribbon(frm) {
    const field = "custom_einvoice_status_notification";
    // Journal Entry: drawn over the form card's top-right corner instead of
    // in a field, so it takes no room from the fields below it.
    const overlay = frm.doctype === "Journal Entry";
    if (overlay ? !frm.get_field("custom_einvoice_item") : !frm.get_field(field)) {
        return;
    }
    einvoice_ui_labels(frm.doc.company).then((labels) => {
        const authority = labels.authority || "Tax Authority";
        const provider = labels.provider_short || "Provider";
        const einvoice = (frm.doc.custom_einvoice_status || "").toUpperCase();
        const reporting = (frm.doc.custom_reporting_status || "").toUpperCase();

        // label, ribbon colour
        let ribbon = null;
        if (einvoice === "FAILED" || reporting === "FAILED" || reporting === "REJECTED") {
            ribbon = [reporting === "REJECTED" ? authority + " Rejected" : authority + " Failed", "#E0232E"];
        } else if (einvoice === "SUCCESS" && reporting === "REPORTED") {
            ribbon = [authority + " Reported", "#23B33A"];
        } else if (einvoice === "SUCCESS" && reporting === "DELIVERED") {
            ribbon = ["Delivered", "#23B33A"];
        } else if (einvoice === "SUCCESS" && reporting === "PENDING") {
            ribbon = [authority + " Pending", "#F29D1C"];
        } else if (einvoice === "SUCCESS" && reporting === "NOT REPORTED") {
            ribbon = ["Not Reported", "#8A8F94"];
        } else if (einvoice === "SUCCESS") {
            ribbon = ["Sent to " + provider, "#2F6FD6"];
        } else if (
            frm.doctype === "Sales Invoice" &&
            frm.doc.docstatus === 1 &&
            labels.einvoice_enabled &&
            !einvoice_has_document_id(frm.doc)
        ) {
            // Submitted, e-invoicing is on for the company, but no e-invoice
            // exists yet (never sent, or the provider did not accept it).
            ribbon = ["E-Invoice Pending", "#F29D1C"];
        } else if (
            frm.doctype === "Journal Entry" &&
            frm.doc.voucher_type === "Credit Note" &&
            !cint(frm.doc.is_system_generated) &&
            !frm.is_new() &&
            frm.doc.docstatus !== 2 &&
            labels.einvoice_enabled &&
            !einvoice_has_document_id(frm.doc)
        ) {
            // Credit Note Journal Entry, saved, no e-invoice yet.
            ribbon = ["E-Invoice Pending", "#F29D1C"];
        }

        let html = " ";
        if (ribbon) {
            const [label, color] = ribbon;
            // Corner geometry: a BOX x BOX clip box, a band BAND wide and
            // HEIGHT tall, whose centre line runs DIST px from the top-right
            // corner. The band's centre sits on the box diagonal, so the
            // label is centred on the visible part of the band, which is
            // 2 * (DIST - HEIGHT / 2) = 156px long on its inner edge.
            const BOX = 150, BAND = 230, HEIGHT = 28, DIST = 92;
            const offset = DIST / Math.SQRT2;
            const top = Math.round(offset - HEIGHT / 2);
            const right = Math.round(offset - BAND / 2);
            const font_size = label.length > 18 ? 11 : 13;
            html =
                '<div style="display:flex;justify-content:flex-end">' +
                '<div style="position:relative;width:' + BOX + "px;height:" + BOX + 'px;overflow:hidden;pointer-events:none">' +
                '<div style="position:absolute;top:' + top + "px;right:" + right + "px;width:" + BAND + "px;transform:rotate(45deg);" +
                "background:" + color + ";color:#ffffff;font-weight:700;font-size:" + font_size + "px;letter-spacing:0.3px;" +
                "white-space:nowrap;text-align:center;line-height:" + HEIGHT + 'px;box-shadow:0 2px 4px rgba(0,0,0,0.25);">' +
                frappe.utils.escape_html(label) +
                "</div>" +
                "</div></div>";
        }
        if (overlay) {
            // The form card itself, below the "Submit this document" message.
            const layout = $(frm.layout.wrapper);
            const page = layout.children(".form-page").first();
            const host = page.length ? page : layout;
            layout.children(".einvoice-ribbon-overlay").remove();
            host.css("position", "relative");
            let box = host.children(".einvoice-ribbon-overlay");
            if (!box.length) {
                box = $(
                    '<div class="einvoice-ribbon-overlay" style="position:absolute;top:0;right:0;z-index:3;' +
                    'pointer-events:none;overflow:hidden;border-top-right-radius:var(--border-radius-lg, 12px)"></div>'
                ).appendTo(host);
            }
            box.html(ribbon ? html : "");
            return;
        }
        frm.set_df_property(field, "options", html);
        frm.refresh_field(field);
    });
}
