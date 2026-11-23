// Status corner ribbon on Sales Invoice (see einvoice_common.js), and the
// Oman-only fields shown only for a company in Oman.
frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        einvoice_render_status_ribbon(frm);
        einvoice_toggle_oman_fields(frm);
    },
    company(frm) {
        einvoice_toggle_oman_fields(frm);
    },
    is_return(frm) {
        einvoice_toggle_oman_fields(frm);
    },
});
