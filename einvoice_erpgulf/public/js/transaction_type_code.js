// Sales Invoice: show the Transaction Type Code (Marmin profile_execution_id)
// live while Transaction Types are picked. The server sets the same value on
// save (countries/ae/transaction_types.py), so this is display only.
(() => {
    const TABLE = "custom_einvoice_transaction_types";
    const CODE = "custom_einvoice_transaction_type_code";
    const OLD = "custom_invoice_transaction_type_code";
    let positions = null;

    const load_positions = async () => {
        if (positions) return positions;
        const rows = await frappe.db.get_list("E-Invoice Transaction Type", {
            fields: ["name", "position"],
            limit: 20,
        });
        positions = {};
        rows.forEach((r) => (positions[r.name] = r.position));
        return positions;
    };

    const show_code = async (frm) => {
        if (!frm.fields_dict[CODE] || !frm.fields_dict[TABLE]) return;
        const pos = await load_positions();
        const bits = "00000000".split("");
        (frm.doc[OLD] || "").split(":")[0].trim().split("").forEach((c, i) => {
            if (c === "1" && i < 8) bits[i] = "1";
        });
        (frm.doc[TABLE] || []).forEach((row) => {
            const p = pos[row.transaction_type];
            if (p >= 1 && p <= 8) bits[p - 1] = "1";
        });
        const code = bits.join("");
        if (frm.doc[CODE] !== code) {
            // Direct assignment: showing the code must not mark the form unsaved.
            frm.doc[CODE] = code;
            frm.refresh_field(CODE);
        }
    };

    frappe.ui.form.on("Sales Invoice", {
        refresh: show_code,
        [TABLE]: show_code,
        [OLD]: show_code,
    });
})();
