from odoo import fields, models, api
from odoo.exceptions import UserError

class AccountMove(models.Model):
    _inherit = "account.move"

    thirdparty_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contacto para factoraje (Pago)",
        compute="_compute_thirdparty_partner_id",
        store=True, # Make it stored for indexing and searching
    )

    @api.depends('payment_ids')
    def _compute_thirdparty_partner_id(self):
        """Find the third-party partner on any payments reconciled with this move."""
        for move in self:
            partner = self.env['res.partner']
            # Search all payments reconciled against the current move's lines
            payments = move.payment_ids
            
            # Use the thirdparty_partner_id from the first payment found
            # You may want to add logic here to ensure only one partner is set
            for payment in payments:
                if payment.thirdparty_partner_id:
                    # Basic check to ensure all payments use the same partner if multiple exist
                    if partner and partner != payment.thirdparty_partner_id:
                        raise UserError(
                            "The invoice '%s' is linked to multiple payments with different factoring partners. This is not supported for CFDI generation." 
                            % move.display_name
                        )
                    partner = payment.thirdparty_partner_id
            
            move.thirdparty_partner_id = partner

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        res = super()._l10n_mx_edi_add_payment_cfdi_values(cfdi_values, pay_results)
        # Pass the computed thirdparty_partner_id to the CFDI rendering context
        cfdi_values["thirdparty_partner_id"] = self.thirdparty_partner_id
        return res