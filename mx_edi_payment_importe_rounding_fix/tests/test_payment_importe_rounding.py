from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.addons.l10n_mx_edi.tests.common import TestMxEdiCommon
from odoo.tests import tagged

PAGO20_NS = {"pago20": "http://www.sat.gob.mx/Pagos20"}


@tagged("post_install_l10n", "post_install", "-at_install")
class TestPaymentImporteRoundingFix(TestMxEdiCommon):
    """ Reproduces CRP20261: the PAC rejects a Payment Complement (REP)
    because 'ImporteDR' of a 'TrasladoDR' node (IEPS 6%) is the invoice's
    accounting tax amount, already rounded to cents, instead of
    'BaseDR x TasaOCuotaDR' computed with 6-decimal precision. For a base of
    1737.88, the accounting amount is 104.27 while the SAT expects
    1737.88 x 0.06 = 104.2728, which is outside the SAT's 0.000001 tolerance
    around the accounting amount.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_6_ieps.type_tax_use = "sale"

    def _create_ieps_invoice(self):
        return self._create_invoice(
            invoice_line_ids=[
                Command.create({
                    "product_id": self.product.id,
                    "price_unit": 1737.88,
                    "tax_ids": [Command.set(self.tax_6_ieps.ids)],
                }),
            ],
        )

    def test_payment_complement_importe_dr_within_sat_tolerance(self):
        with freeze_time("2017-01-07"):
            invoice = self._create_ieps_invoice()

            with self.with_mocked_pac_sign_success():
                invoice._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertEqual(invoice.l10n_mx_edi_cfdi_state, "sent")

            # Full payment: percentage_paid = 100%, so the tax amount reused
            # for the payment complement is exactly the invoice's accounting
            # amount.
            payment = self._create_payment(invoice, amount=invoice.amount_residual)

            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            payment_document = payment.move_id.l10n_mx_edi_payment_document_ids.sorted()[:1]
            self.assertEqual(payment_document.state, "payment_sent")

            tree = etree.fromstring(payment_document.attachment_id.raw)

        ieps_traslados_dr = [
            node
            for node in tree.findall(".//pago20:TrasladoDR", PAGO20_NS)
            if node.get("ImpuestoDR") == "003"
        ]
        self.assertTrue(ieps_traslados_dr, "Expected an IEPS TrasladoDR node.")

        for node in ieps_traslados_dr:
            base = float(node.get("BaseDR"))
            rate = float(node.get("TasaOCuotaDR"))
            importe = float(node.get("ImporteDR"))
            # Same tolerance the SAT enforces for CRP20261.
            self.assertLessEqual(
                abs(base * rate - importe),
                0.000001,
                f"ImporteDR={importe} does not match BaseDR x TasaOCuotaDR="
                f"{base * rate} within the SAT's tolerance (CRP20261).",
            )

        ieps_traslados_p = [
            node
            for node in tree.findall(".//pago20:TrasladoP", PAGO20_NS)
            if node.get("ImpuestoP") == "003"
        ]
        self.assertTrue(ieps_traslados_p, "Expected an aggregated IEPS TrasladoP node.")

        for node in ieps_traslados_p:
            base = float(node.get("BaseP"))
            rate = float(node.get("TasaOCuotaP"))
            importe = float(node.get("ImporteP"))
            self.assertLessEqual(abs(base * rate - importe), 0.000001)
