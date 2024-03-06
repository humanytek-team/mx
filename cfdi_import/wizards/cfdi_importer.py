import base64
import logging
import traceback

import xmltodict
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

force_list = [
    "Concepto",
    "Traslado",
    "Retencion",
]


class namespaces:
    def get(self, _key, _default):
        return None

    def __getitem__(self, _key):
        return None


class CFDIImporter(models.TransientModel):
    _name = "cfdi_importer"
    _description = "CFDI Importer"

    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    xml_ids = fields.Many2many(
        string="XMLs",
        comodel_name="ir.attachment",
    )
    errors = fields.Text(
        readonly=True,
    )
    move_ids = fields.Many2many(
        comodel_name="account.move",
        readonly=True,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        required=True,
        # TODO domain
        # TODO default
    )

    def improve_cfdi(self, cfdi):
        cfdi["@UUID"] = cfdi["Complemento"]["TimbreFiscalDigital"]["@UUID"]

        if cfdi["@Version"] not in ("3.3", "4.0"):
            raise ValueError(_("The CFDI %s version is not supported") % cfdi["@UUID"])

        if cfdi["@TipoDeComprobante"] not in ("I"):
            raise ValueError(_("The CFDI %s type is not supported") % cfdi["@UUID"])

        rfc_receptor = cfdi["Receptor"]["@Rfc"]
        rfc_emisor = cfdi["Emisor"]["@Rfc"]
        other_rfc = ""
        other = None
        issued = False
        if self.company_id.vat == rfc_receptor:
            other_rfc = rfc_emisor
            other = cfdi["Emisor"]
            issued = True
        elif self.company_id.vat == rfc_emisor:
            other_rfc = rfc_receptor
            other = cfdi["Receptor"]
        if not other_rfc:
            raise ValueError(
                _("The CFDI %s does not belong to this company") % cfdi["@UUID"]
            )
        cfdi["other"] = other
        cfdi["issued"] = issued

    def get_cfdi(self, xml: str):
        cfdi = xmltodict.parse(
            xml,
            process_namespaces=True,
            namespaces=namespaces(),
            force_list=force_list,
        )["Comprobante"]
        self.improve_cfdi(cfdi)
        return cfdi

    def get_move(self, uuid: str):
        return self.env["account.move"].search(
            [("l10n_mx_edi_cfdi_uuid", "=", uuid)], limit=1
        )

    def get_or_create_move(self, cfdi, xml):
        move = self.get_move(cfdi["@UUID"])
        if move:
            return move
        return self.create_move(cfdi, xml)

    def import_xml(self, xml: str):
        cfdi = self.get_cfdi(xml)
        move = self.get_or_create_move(cfdi, xml)
        return move

    def get_or_create_partner(self, cfdi):
        partner = self.env["res.partner"].search(
            [("vat", "=", cfdi["other"]["@Rfc"])], limit=1
        )
        if not partner:
            partner = self.create_partner(cfdi)
        return partner

    def create_partner(self, cfdi):
        partner = self.env["res.partner"].create(
            {
                "vat": cfdi["other"]["@Rfc"],
                "name": cfdi["other"].get("@Nombre"),
                "zip": cfdi["other"].get("@DomicilioFiscalReceptor"),
                "country_id": self.env.ref("base.mx").id,
            }
        )
        return partner

    def create_move(self, cfdi, xml):
        partner = self.get_or_create_partner(cfdi)
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{cfdi['@UUID']}.xml",
                "datas": base64.b64encode(xml.encode("utf-8")),
                "mimetype": "text/xml",
            }
        )
        move = self.env["account.move"].create(
            {
                "l10n_mx_edi_cfdi_attachment_id": attachment.id,
                "partner_id": partner.id,
                "journal_id": self.journal_id.id,
                "company_id": self.company_id.id,
                "move_type": "in_invoice" if cfdi["issued"] else "out_invoice",
            }
        )
        return move

    def action_import_cfdis(self):
        self.ensure_one()
        self.errors = ""
        for attachment in self.xml_ids:
            try:
                xml = base64.b64decode(attachment.datas).decode("utf-8")
                self.move_ids += self.import_xml(xml)
            except Exception as e:
                trace = traceback.format_exc()
                self.errors += f"""\
# Error importing {attachment.name}:
# {e}
{trace}
"""
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

        return {  # TODO remove
            "type": "ir.actions.act_window",
            "res_model": "cfdi_importer",
            "view_mode": "form",
            "view_id": self.env.ref("cfdi_import.cfdi_importer_wizard").id,
            "target": "new",
            "res_id": self.id,
        }
        # return {"type": "ir.actions.act_window_close"}