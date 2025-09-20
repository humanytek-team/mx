from odoo import api, models


class Partner(models.Model):
    _inherit = "res.partner"

    @api.depends("is_company", "parent_id.commercial_partner_id")
    def _compute_commercial_partner(self):
        super()._compute_commercial_partner()
        for partner in self:
            invoice_address = partner.address_get(["invoice"]).get("invoice")
            if partner.is_company or not partner.parent_id:
                partner.commercial_partner_id = invoice_address or partner
            else:
                partner.commercial_partner_id = (
                    invoice_address or partner.parent_id.commercial_partner_id
                )
