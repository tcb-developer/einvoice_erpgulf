// Oman-only fields on Address and Item Tax Template stay hidden until a
// Company in Oman has e-invoicing switched on (checked once per session).
window.einvoice_oman_active = window.einvoice_oman_active || null;

function einvoice_oman_field_toggle(frm, fields) {
    const present = fields.filter((f) => frm.get_field(f));
    if (!present.length) {
        return;
    }
    const apply = (active) => frm.toggle_display(present, Boolean(active));
    if (window.einvoice_oman_active !== null) {
        apply(window.einvoice_oman_active);
        return;
    }
    apply(false);
    frappe
        .call({ method: "einvoice_erpgulf.core.registry.oman_einvoicing_active" })
        .then((r) => {
            window.einvoice_oman_active = Boolean(r.message);
            apply(window.einvoice_oman_active);
        })
        .catch(() => apply(false));
}

frappe.ui.form.on("Address", {
    refresh(frm) {
        einvoice_oman_field_toggle(frm, ["custom_governorate", "custom_om_special_zone"]);
    },
});

frappe.ui.form.on("Item Tax Template", {
    refresh(frm) {
        einvoice_oman_field_toggle(frm, ["custom_om_vat_exemption_reason_code", "custom_om_zero_rating_reason_code"]);
    },
});
