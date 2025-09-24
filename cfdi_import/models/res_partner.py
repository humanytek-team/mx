from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ignore_import_xml = fields.Boolean(string="Ignore partner in XML Importer")
