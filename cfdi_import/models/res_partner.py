from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ignore_import_xml = fields.Bool(string="Ignore partner in XML Importer")
