import base64
import logging
import traceback
from typing import Any

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
        compute="_compute_journal_id",
        store=True,
        readonly=False,
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        required=True,
    )
    suitable_journal_ids = fields.Many2many(
        comodel_name="account.journal",
        compute="_compute_suitable_journal_ids",
    )

    @api.depends("company_id")
    def _compute_suitable_journal_ids(self):
        for record in self:
            company = record.company_id or self.env.company
            record.suitable_journal_ids = self.env["account.journal"].search(
                [
                    *self.env["account.journal"]._check_company_domain(company),
                    ("type", "in", ["purchase", "sale", "general"]),
                ]
            )

    @api.depends("suitable_journal_ids")
    def _compute_journal_id(self):
        for record in self:
            sale_journals = record.suitable_journal_ids.filtered(
                lambda j: j.type == "sale"
            )
            record.journal_id = sale_journals and sale_journals[0]

    def get_issued_info(self, cfdi) -> tuple[bool, dict[str, Any]]:
        """Return if the CFDI was issued by the company and the other party."""
        company_rfc = self.company_id.vat
        receptor, emisor = cfdi["Receptor"], cfdi["Emisor"]
        if company_rfc not in (receptor["@Rfc"], emisor["@Rfc"]):
            raise ValueError(
                _("The CFDI %s does not belong to this company") % cfdi["@UUID"]
            )
        issued = company_rfc == emisor["@Rfc"]
        other = receptor if issued else emisor
        return issued, other

    def improve_cfdi(self, cfdi):
        cfdi["@UUID"] = cfdi["Complemento"]["TimbreFiscalDigital"]["@UUID"]

        supported_versions = ("3.3", "4.0")
        if cfdi["@Version"] not in supported_versions:
            raise ValueError(
                _("The CFDI %s version (%s) is not supported, only %s")
                % (cfdi["@UUID"], cfdi["@Version"], supported_versions)
            )

        supported_types = ("I",)
        if cfdi["@TipoDeComprobante"] not in supported_types:
            raise ValueError(
                _("The CFDI %s type (%s) is not supported, only %s")
                % (cfdi["@UUID"], cfdi["@TipoDeComprobante"], supported_types)
            )

        issued, other = self.get_issued_info(cfdi)
        cfdi["issued"] = issued
        cfdi["other"] = other

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

    def get_partner(self, cfdi):
        return self.env["res.partner"].search(
            [("vat", "=", cfdi["other"]["@Rfc"])], limit=1
        )

    def get_or_create_partner(self, cfdi):
        partner = self.get_partner(cfdi)
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

    def _get_tax(self, xml_tax, cfdi, sign=1):
        amount = float(xml_tax["@TasaOCuota"]) * 100 * sign
        tax = self.env["account.tax"].search(
            [
                ("amount", "=", amount),
                ("type_tax_use", "=", "sale" if cfdi["issued"] else "purchase"),
                ("company_id", "parent_of", self.company_id.id),
                ("country_id", "=", self.env.ref("base.mx").id),
            ]
        )
        if not tax:
            raise ValueError(_("The tax %s is not available") % xml_tax["@TasaOCuota"])
        if len(tax) > 1:
            _logger.warning(
                "Multiple taxes found for %s, using the first one",
                xml_tax["@TasaOCuota"],
            )
            tax = tax[0]
        return tax

    def get_taxes(self, cfdi, concepto):
        taxes = self.env["account.tax"].browse()
        for xml_tax in (
            concepto.get("Impuestos", {}).get("Traslados", {}).get("Traslado", [])
        ):
            taxes |= self._get_tax(xml_tax, cfdi)
        for xml_tax in (
            concepto.get("Impuestos", {}).get("Retenciones", {}).get("Retencion", [])
        ):
            taxes |= self._get_tax(xml_tax, cfdi, -1)
        return taxes

    def create_lines(self, cfdi):
        lines = []
        for concepto in cfdi["Conceptos"]["Concepto"]:
            taxes = self.get_taxes(cfdi, concepto)
            discount_amount = float(concepto.get("@Descuento", 0))
            importe = float(concepto["@Importe"])
            discount_percent = importe and discount_amount / importe * 100
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": concepto["@Descripcion"],
                        "quantity": concepto["@Cantidad"],
                        "price_unit": concepto["@ValorUnitario"],
                        "discount": discount_percent,
                        "tax_ids": [(6, 0, taxes.ids)],
                        "account_id": self.account_id.id,
                    },
                )
            )
        return lines

    def check_journal(self, cfdi, journal):
        if cfdi["issued"] and journal.type != "sale":
            raise ValueError(
                _("The CFDI %s is issued but the journal is not a sale journal")
                % cfdi["@UUID"]
            )
        if not cfdi["issued"] and journal.type != "purchase":
            raise ValueError(
                _("The CFDI %s is received but the journal is not a purchase journal")
                % cfdi["@UUID"]
            )

    def get_ref(self, cfdi):
        serie = cfdi.get("@Serie", "")
        folio = cfdi.get("@Folio", "")
        sep = "-" if serie and folio else ""
        return f"{serie}{sep}{folio}"

    def create_move(self, cfdi, xml):
        partner = self.get_or_create_partner(cfdi)
        lines = self.create_lines(cfdi)
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{cfdi['@UUID']}.xml",
                "datas": base64.b64encode(xml.encode("utf-8")),
                "mimetype": "text/xml",
            }
        )
        l10n_mx_edi_payment_method = self.env["l10n_mx_edi.payment.method"].search(
            [("code", "=", cfdi.get("@FormaPago"))],
            limit=1,
        )
        currency = (
            self.env["res.currency"].search(
                [("name", "=", cfdi.get("@Moneda"))],
                limit=1,
            )
            or self.env.company.currency_id
        )

        self.check_journal(cfdi, self.journal_id)

        move = self.env["account.move"].create(
            {
                "journal_id": self.journal_id.id,
                "company_id": self.company_id.id,
                "partner_id": partner.id,
                "move_type": "out_invoice" if cfdi["issued"] else "in_invoice",
                "invoice_date": cfdi["@Fecha"],
                "line_ids": lines,
                "l10n_mx_edi_payment_policy": cfdi.get("@MetodoPago"),
                "l10n_mx_edi_usage": cfdi["other"].get("@UsoCFDI"),
                "l10n_mx_edi_cfdi_uuid": cfdi["@UUID"],
                "l10n_mx_edi_payment_method_id": l10n_mx_edi_payment_method.id,
                "currency_id": currency.id,
                "ref": self.get_ref(cfdi),
            }
        )

        document = move._l10n_mx_edi_cfdi_invoice_document_sent(
            attachment.name, xml.encode("utf-8")
        )
        move.with_context(no_new_invoice=True).message_post(
            body=_(
                "The CFDI document was successfully created and signed by the government."
            ),
            attachment_ids=document.attachment_id.ids,
        )
        documents = move.l10n_mx_edi_invoice_document_ids
        self.env["l10n_mx_edi.document"]._fetch_and_update_sat_status(
            extra_domain=[("id", "in", documents.ids)]
        )
        try:
            move.action_post()
        except Exception as e:
            _logger.exception(e)
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
                _logger.error(
                    "Error importing %s: %s\n%s",
                    attachment.name,
                    e,
                    trace,
                )
                self.errors += f"""\
# Error importing {attachment.name}:
# {e}
{trace}
"""
            else:
                attachment.unlink()
        return {
            "type": "ir.actions.act_window",
            "res_model": "cfdi_importer",
            "view_mode": "form",
            "view_id": self.env.ref("cfdi_import.cfdi_importer_wizard").id,
            "target": "new",
            "res_id": self.id,
        }
