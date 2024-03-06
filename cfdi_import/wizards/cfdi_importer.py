from odoo import _, api, fields, models


class CFDIImporter(models.TransientModel):
    _name = "cfdi_importer"
    _description = "CFDI Importer"

    file = fields.Binary(
        required=True,
    )
    file_name = fields.Char(
        required=True,
    )

    def action_import_cfdis(self):
        self.ensure_one()
