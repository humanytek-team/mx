from odoo import _, api, fields, models


class CFDIImporter(models.TransientModel):
    _name = "cfdi_importer"
    _description = "CFDI Importer"

    xml_ids = fields.Many2many(
        string="XMLs",
        comodel_name="ir.attachment",
    )
    errors = fields.Html(
        readonly=True,
    )

    def action_import_cfdis(self):
        self.ensure_one()
        self.errors = ""
        for xml in self.xml_ids:
            try:
                self.import_xml(xml.datas)
            except Exception as e:
                self.errors += f"<p>{e}</p>"
        # If errors, show them in the same wizard
        if self.errors:
            return {
                "type": "ir.actions.act_window",
                "res_model": "cfdi_importer",
                "view_mode": "form",
                "view_id": self.env.ref("cfdi_import.cfdi_importer_wizard").id,
                "target": "new",
                "res_id": self.id,
            }
        # If no errors, close the wizard
        return {"type": "ir.actions.act_window_close"}
