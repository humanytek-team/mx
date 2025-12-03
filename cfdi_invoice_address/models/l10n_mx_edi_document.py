import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class L10nMxEdiDocument(models.Model):
    _inherit = "l10n_mx_edi.document"

    @api.model
    def _add_certificate_cfdi_values(self, cfdi_values):
        super()._add_certificate_cfdi_values(cfdi_values)
        self.sudo().set_supplier_from_partner(cfdi_values)

    def set_supplier_from_partner(self, cfdi_values):
        # If company has invoicing contact, use it as supplier
        root_company = cfdi_values["root_company"]
        supplier = root_company.partner_id.commercial_partner_id.with_user(
            self.env.user
        )
        invoice_partner_id = supplier.address_get(["invoice"]).get("invoice")
        if not invoice_partner_id:
            return
        invoice_partner = self.env["res.partner"].browse(invoice_partner_id)
        final_partner = invoice_partner or supplier
        _logger.info(
            "Using %s as supplier for CFDI generation instead of company %s",
            final_partner.id,
            supplier.id,
        )
        cfdi_values["emisor"].update(
            {
                "supplier": final_partner,
                "rfc": final_partner.vat,
                "nombre": self._cfdi_sanitize_to_legal_name(final_partner.name),
                "domicilio_fiscal_receptor": final_partner.zip,
            }
        )
