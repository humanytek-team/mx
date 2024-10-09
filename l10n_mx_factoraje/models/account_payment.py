from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    thirdparty_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contacto para factoraje",
    )
