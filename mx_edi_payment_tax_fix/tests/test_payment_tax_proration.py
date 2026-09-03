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

    def test_payment_complement_without_credit_note_keeps_sat_precision(self):
        """ Regression test for SAT error #CRP20261.

        An earlier version of this module recomputed the tax breakdown for
        EVERY invoice unconditionally (even with no credit note involved)
        and rounded 'base'/'importe' down to currency precision (2
        decimals). That discarded the 6-decimal precision the SAT requires
        for TrasladoDR/RetencionDR, so 'ImporteDR' stopped matching
        'BaseDR x TasaOCuotaDR' within the SAT's 0.000001 tolerance (e.g.
        1737.88 x 0.06 = 104.2728 but Odoo emitted 104.27).

        With no credit note involved, this module must leave Odoo's own
        (correct) breakdown untouched.
        """
        with freeze_time("2017-01-07"):
            invoice = self._create_invoice(
                invoice_line_ids=[
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 1737.88,
                        "tax_ids": [Command.set(self.tax_6_ieps.ids)],
                    }),
                ],
            )
            with self.with_mocked_pac_sign_success():
                invoice._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertEqual(invoice.l10n_mx_edi_cfdi_state, "sent")

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
        self.assertTrue(ieps_traslados_dr)

        for node in ieps_traslados_dr:
            base = float(node.get("BaseDR"))
            rate = float(node.get("TasaOCuotaDR"))
            importe = float(node.get("ImporteDR"))
            self.assertLessEqual(
                abs(base * rate - importe),
                0.000001,
                f"ImporteDR={importe} does not match BaseDR x TasaOCuotaDR="
                f"{base * rate} within the SAT's tolerance (CRP20261).",
            )

    def test_payment_totals_base_matches_sum_of_related_documents_bases(self):
        """ Regression test for SAT error #CRP20268.

        '_mx_edi_payment_tax_fix_recompute_totals' rebuilds each aggregated
        TrasladoP/RetencionP node by summing the (raw, double-precision)
        'base' of every DoctoRelacionado sharing the same tax/rate, then
        rounds that SUM to 6 decimals. But each DoctoRelacionado's own
        BaseDR is rounded to 6 decimals independently when rendered. Those
        two roundings can disagree: 17 untouched invoice lines, several
        carrying a few units of floating-point noise below the 6th decimal
        (e.g. from Odoo's own upstream tax computation), each round to the
        *same* BaseDR shown in the XML, but summing the noisy raw floats
        first crosses a rounding boundary that summing the already-rounded
        BaseDR values never would - so BaseP came out one micro-unit higher
        than 'sum(BaseDR)', e.g. in a real production CFDI:

            BaseP="113352.470196" vs sum(BaseDR) == 113352.470195

        which is exactly the mismatch SAT's rule CRP20268 rejects: BaseP
        must equal the sum of the BaseDR of every related document whose
        ImpuestoDR/TasaOCuotaDR match this node's ImpuestoP/TasaOCuotaP.

        The fix is to round each DoctoRelacionado's base to 6 decimals
        BEFORE accumulating it into the aggregate, i.e. sum what will
        actually be displayed as BaseDR, not the raw upstream floats.
        """
        company = self.company_data["company"]

        # The 17 "IVA 0%" bases from the real rejected CFDI
        # (BBVA1-PBBVA1202605892-MX-Payment-20.xml).
        displayed_bases = [
            14103.531687, 8485.161600, 32577.598392, 6241.605200,
            7973.143953, 148.144540, 740.722700, 1933.075269,
            1793.699700, 2774.236770, 8916.020100, 4483.678100,
            2034.408600, 11025.955168, 4942.563760, 159.569856,
            5019.354800,
        ]
        # Two of them perturbed by a few units of sub-micro-unit
        # floating-point noise (as Odoo's own upstream tax computation
        # would produce): individually they still round to the exact
        # BaseDR shown in that XML, but their raw (unrounded) sum rounds
        # to one micro-unit more.
        raw_bases = list(displayed_bases)
        raw_bases[0] += 0.0000003
        raw_bases[5] += 0.0000003

        expected_base_dr = [round(base, 6) for base in displayed_bases]
        self.assertEqual(expected_base_dr, displayed_bases)
        self.assertEqual(
            sum(expected_base_dr),
            113352.470195,
            "sanity check: this is the BaseP the SAT rule CRP20268 expects.",
        )

        cfdi_values = {
            "company": company,
            "tipo_cambio": 1.0,
            "docto_relationado_list": [
                {
                    "equivalencia": 1.0,
                    "retenciones_list": [],
                    "local_retenciones_list": [],
                    "local_traslados_list": [],
                    "traslados_list": [{
                        "impuesto": "002",
                        "tipo_factor": "Tasa",
                        "tasa_o_cuota": 0.0,
                        "base": base,
                        "importe": 0.0,
                    }],
                }
                for base in raw_bases
            ],
        }

        self.env["account.move"].new()._mx_edi_payment_tax_fix_recompute_totals(cfdi_values)

        traslado_p = next(
            tax_values
            for tax_values in cfdi_values["traslados_list"]
            if tax_values["impuesto"] == "002"
        )
        self.assertAlmostEqual(
            traslado_p["base"],
            sum(expected_base_dr),
            places=6,
            msg=(
                "BaseP must equal the sum of the (rounded) BaseDR of every "
                "related document with the same ImpuestoDR/TasaOCuotaDR "
                "(SAT rule CRP20268)."
            ),
        )
