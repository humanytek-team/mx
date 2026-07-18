from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.addons.l10n_mx_edi.tests.common import TestMxEdiCommon
from odoo.tests import tagged

PAGO20_NS = {"pago20": "http://www.sat.gob.mx/Pagos20"}


@tagged("post_install_l10n", "post_install", "-at_install")
class TestPaymentTaxProrationFix(TestMxEdiCommon):
    """ Reproduces the reported scenario:

    An invoice has two lines: one taxed with IEPS 6% and one without IEPS.
    A credit note fully refunds the IEPS line. The Payment Complement (CFDI
    de Pago) generated for the remaining balance must NOT keep prorating the
    already-cancelled IEPS, and its totals must reconcile with what is
    actually still open.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_6_ieps.type_tax_use = "sale"

    def _create_two_line_invoice(self):
        return self._create_invoice(
            invoice_date="2017-01-01",
            date="2017-01-01",
            invoice_date_due="2017-03-01",
            invoice_line_ids=[
                Command.create({
                    "product_id": self.product.id,
                    "price_unit": 1000.0,
                    "tax_ids": [Command.set((self.tax_16 + self.tax_6_ieps).ids)],
                }),
                Command.create({
                    "product_id": self.product.id,
                    "price_unit": 1000.0,
                    "tax_ids": [Command.set(self.tax_16.ids)],
                }),
            ],
        )

    def _create_full_refund_of_ieps_line(self, invoice):
        """ Credit note refunding only the IEPS-taxed line, reconciled
        against the invoice (mirrors what the 'Add Credit Note' wizard does
        when refunding a single line). """
        credit_note = self.env["account.move"].create({
            "move_type": "out_refund",
            "partner_id": invoice.partner_id.id,
            "invoice_date": invoice.invoice_date,
            "date": invoice.date,
            "reversed_entry_id": invoice.id,
            "l10n_mx_edi_payment_method_id": invoice.l10n_mx_edi_payment_method_id.id,
            "currency_id": invoice.currency_id.id,
            "invoice_line_ids": [
                Command.create({
                    "product_id": self.product.id,
                    "price_unit": 1000.0,
                    "tax_ids": [Command.set((self.tax_16 + self.tax_6_ieps).ids)],
                }),
            ],
        })
        credit_note.action_post()

        with self.with_mocked_pac_sign_success():
            credit_note._l10n_mx_edi_cfdi_invoice_try_send()

        return credit_note

    def test_payment_complement_excludes_credited_ieps(self):
        with freeze_time("2017-01-07"):
            invoice = self._create_two_line_invoice()

            with self.with_mocked_pac_sign_success():
                invoice._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertEqual(invoice.l10n_mx_edi_cfdi_state, "sent")

            self._create_full_refund_of_ieps_line(invoice)

            # Only the IEPS-free line remains open: 1000 + 16% VAT = 1160.
            self.assertAlmostEqual(invoice.amount_residual, 1160.0, places=2)

            payment = self._create_payment(invoice, amount=invoice.amount_residual)

            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            payment_document = payment.move_id.l10n_mx_edi_payment_document_ids.sorted()[:1]
            self.assertEqual(payment_document.state, "payment_sent")

            tree = etree.fromstring(payment_document.attachment_id.raw)

        # -- Per-document (DR) tax breakdown: no leftover IEPS proration.
        # The node must be entirely absent, not zero-valued: SAT rejects a
        # TrasladoDR/TrasladoP whose BaseDR/BaseP is 0 (error #CRP20255).
        ieps_traslados_dr = [
            node
            for node in tree.findall(".//pago20:TrasladoDR", PAGO20_NS)
            if node.get("ImpuestoDR") == "003"
        ]
        self.assertFalse(
            ieps_traslados_dr,
            "IEPS must be entirely absent from the payment complement: it was "
            "fully cancelled by the credit note before this payment, and SAT "
            "rejects a zero-valued Traslado node (#CRP20255).",
        )

        # -- Aggregated (P) tax breakdown: IEPS must be absent too. --
        ieps_traslados_p = [
            node
            for node in tree.findall(".//pago20:TrasladoP", PAGO20_NS)
            if node.get("ImpuestoP") == "003"
        ]
        self.assertFalse(ieps_traslados_p)

        # -- The remaining VAT-only base/tax must reconcile with the amount paid. --
        docto_relacionado = tree.find(".//pago20:DoctoRelacionado", PAGO20_NS)
        self.assertAlmostEqual(float(docto_relacionado.get("ImpSaldoAnt")), 1160.0, places=2)
        self.assertAlmostEqual(float(docto_relacionado.get("ImpPagado")), 1160.0, places=2)
        self.assertAlmostEqual(float(docto_relacionado.get("ImpSaldoInsoluto")), 0.0, places=2)

        traslados_dr = tree.findall(".//pago20:TrasladoDR", PAGO20_NS)
        iva_traslado_dr = next(node for node in traslados_dr if node.get("ImpuestoDR") == "002")
        self.assertAlmostEqual(float(iva_traslado_dr.get("BaseDR")), 1000.0, places=2)
        self.assertAlmostEqual(float(iva_traslado_dr.get("ImporteDR")), 160.0, places=2)

        totales = tree.find(".//pago20:Totales", PAGO20_NS)
        self.assertAlmostEqual(float(totales.get("MontoTotalPagos")), 1160.0, places=2)
