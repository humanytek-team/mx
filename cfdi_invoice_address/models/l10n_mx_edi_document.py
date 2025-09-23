from odoo import api, models


class L10nMxEdiDocument(models.Model):
    _inherit = "l10n_mx_edi.document"

    @api.model
    def _add_certificate_cfdi_values(self, cfdi_values):
        super()._add_certificate_cfdi_values(cfdi_values)
        # If company has invoicing contact, use it as supplier
        root_company = cfdi_values["root_company"]
        supplier = root_company.partner_id.commercial_partner_id.with_user(
            self.env.user
        )
        invoice_partner_id = supplier.address_get(["invoice"]).get("invoice")
        if not invoice_partner_id:
            return
        invoice_partner = self.env["res.partner"].browse(invoice_partner_id)
        cfdi_values["emisor"].update(
            {
                "supplier": invoice_partner or supplier.vat,
                "rfc": invoice_partner.vat or supplier.vat,
                "nombre": self._cfdi_sanitize_to_legal_name(
                    invoice_partner.name or supplier.vat
                ),
                "domicilio_fiscal_receptor": invoice_partner.zip or supplier.zip,
            }
        )
