from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    thirdparty_partner_id = fields.Many2one(
        related="payment_id.thirdparty_partner_id",
    )

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        res = super()._l10n_mx_edi_add_payment_cfdi_values(cfdi_values, pay_results)
        cfdi_values["thirdparty_partner_id"] = self.thirdparty_partner_id
        return res
