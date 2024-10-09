from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    thirdparty_partner_id = fields.Many2one(
        related="payment_id.thirdparty_partner_id",
    )
