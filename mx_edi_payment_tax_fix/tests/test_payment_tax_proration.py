from decimal import Decimal
from unittest.mock import patch

from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.addons.l10n_mx_edi.tests.common import TestMxEdiCommon
from odoo.addons.mx_edi_payment_tax_fix.models.account_move import AccountMove
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
        """ Unit-level check for SAT error #CRP20268 on the aggregation
        itself: given the per-document (DR) 'base' values Odoo actually
        renders (real numbers from BBVA1-PBBVA1202605892-MX-Payment-20.xml,
        already at the 6-decimal precision the SAT expects for BaseDR),
        '_mx_edi_payment_tax_fix_recompute_totals' must aggregate the
        top-level TrasladoP/RetencionP 'base' to exactly their sum - which
        is what SAT rule CRP20268 checks (BaseP must equal the sum of the
        BaseDR of every related document sharing the same tax/rate).
        """
        company = self.company_data["company"]

        # The 17 "IVA 0%" BaseDR from the real rejected CFDI.
        base_dr_values = [
            14103.531687, 8485.161600, 32577.598392, 6241.605200,
            7973.143953, 148.144540, 740.722700, 1933.075269,
            1793.699700, 2774.236770, 8916.020100, 4483.678100,
            2034.408600, 11025.955168, 4942.563760, 159.569856,
            5019.354800,
        ]

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
                for base in base_dr_values
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
            sum(base_dr_values),
            places=6,
            msg=(
                "BaseP must equal the sum of the BaseDR of every related "
                "document with the same ImpuestoDR/TasaOCuotaDR "
                "(SAT rule CRP20268)."
            ),
        )

    def test_totals_recomputed_even_without_any_credit_note(self):
        """ Regression test for SAT error #CRP20268 (structural cause).

        Odoo's native '_l10n_mx_edi_add_payment_cfdi_values' computes the
        per-document DR breakdown and the aggregated P totals via two
        INDEPENDENT summations: the DR breakdown is rounded to 6 decimals
        per invoice, while the P totals are summed from raw (unrounded)
        tax-base-line amounts across every invoice in the payment and
        rounded only once, at the end. Those two roundings can disagree by
        a single micro-unit from ordinary floating-point noise, with no
        credit note anywhere involved - this is what happened with a real
        17-invoice batch payment (BBVA1-PBBVA1202605892-MX-Payment-20.xml):
        BaseP="113352.470196" vs the correct sum of BaseDR, 113352.470195,
        even though not one of those 17 invoices had a credit note.

        An earlier version of this module only rebuilt the P totals when at
        least one invoice in the payment had a related credit note
        (`if not invoice._l10n_mx_edi_get_invoice_related_credit_notes():
        continue` short-circuited the whole batch to Odoo's native,
        independently-rounded totals otherwise). That gate is exactly why
        deploying the CRP20268 arithmetic fix above did not resolve the
        production error: this batch has no credit notes, so the totals
        recompute never ran. '_mx_edi_payment_tax_fix_recompute_totals'
        must always run, credit note or not.
        """
        with freeze_time("2017-01-07"):
            invoice = self._create_invoice(
                invoice_line_ids=[
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 1000.0,
                        "tax_ids": [Command.set(self.tax_16.ids)],
                    }),
                ],
            )
            with self.with_mocked_pac_sign_success():
                invoice._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertFalse(
                invoice._l10n_mx_edi_get_invoice_related_credit_notes(),
                "sanity check: this invoice has no credit note.",
            )

            payment = self._create_payment(invoice, amount=invoice.amount_residual)

            with patch.object(
                AccountMove,
                "_mx_edi_payment_tax_fix_recompute_totals",
                autospec=True,
                wraps=AccountMove._mx_edi_payment_tax_fix_recompute_totals,
            ) as recompute_mock:
                with self.with_mocked_pac_sign_success():
                    invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            payment_document = payment.move_id.l10n_mx_edi_payment_document_ids.sorted()[:1]
            self.assertEqual(payment_document.state, "payment_sent")

        recompute_mock.assert_called_once()

    def test_credit_note_split_over_several_invoices_never_goes_negative(self):
        """ Regression test for SAT error #CRP20255.

        Reproduces the rejected production CFDI
        BBVA1-PBBVA1202605893-MX-Payment-20.xml, whose related document
        carried:

            TrasladoDR BaseDR="-2971.890000" ImpuestoDR="003" TipoFactorDR="Exento"
            TrasladoDR BaseDR="43121.360000" ImpuestoDR="002" TasaOCuotaDR="0.000000"

        with ImpPagado="19437.15" - a NEGATIVE base (which SAT rejects: "El
        valor del campo BaseDR ... debe ser mayor que cero"), and a base 2.2x
        larger than the amount actually paid.

        Both symptoms come from the same cause: this module netted out each
        credit note's FULL tax breakdown and FULL amount_total, even when
        only part of that credit note was reconciled against this invoice
        (the rest belonging to other invoices). Over-subtracting drives the
        exempt group's base below zero AND shrinks 'open_amount_total', which
        pushes 'percentage_paid' above 1.0 and inflates every base.

        Here a 3,000 credit note is split 2,000 / 1,000 over two invoices, so
        only a third of it may be netted out of the invoice being paid.
        """
        with freeze_time("2017-01-07"):
            # The invoice being paid: 5,000 at IVA 0% + 1,000 exempt.
            invoice = self._create_invoice(
                invoice_line_ids=[
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 5000.0,
                        "tax_ids": [Command.set(self.tax_0.ids)],
                    }),
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 1000.0,
                        "tax_ids": [Command.set(self.tax_0_exento.ids)],
                    }),
                ],
            )
            with self.with_mocked_pac_sign_success():
                invoice._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertAlmostEqual(invoice.amount_total, 6000.0, places=2)

            # Another invoice the same credit note is also applied to.
            other_invoice = self._create_invoice(
                invoice_line_ids=[
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 2000.0,
                        "tax_ids": [Command.set(self.tax_0_exento.ids)],
                    }),
                ],
            )

            # One 3,000 exempt credit note for both invoices: its exempt base
            # (3,000) is three times the exempt base of the invoice being paid.
            credit_note = self.env["account.move"].create({
                "move_type": "out_refund",
                "partner_id": invoice.partner_id.id,
                "invoice_date": invoice.invoice_date,
                "date": invoice.date,
                "l10n_mx_edi_payment_method_id": invoice.l10n_mx_edi_payment_method_id.id,
                "currency_id": invoice.currency_id.id,
                "invoice_line_ids": [
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 3000.0,
                        "tax_ids": [Command.set(self.tax_0_exento.ids)],
                    }),
                ],
            })
            credit_note.action_post()
            with self.with_mocked_pac_sign_success():
                credit_note._l10n_mx_edi_cfdi_invoice_try_send()

            # Apply 2,000 of the credit note to the other invoice first, so
            # only the remaining 1,000 lands on the invoice being paid.
            def receivable_lines(*moves):
                lines = self.env["account.move.line"]
                for move in moves:
                    lines |= move.line_ids.filtered(lambda x: x.account_type == "asset_receivable")
                return lines

            receivable_lines(other_invoice, credit_note).reconcile()
            receivable_lines(invoice, credit_note).reconcile()

            self.assertAlmostEqual(
                invoice._l10n_mx_edi_get_invoice_credit_notes_amounts()[credit_note],
                1000.0,
                places=2,
                msg="only 1,000 of the 3,000 credit note was applied to this invoice.",
            )
            # 6,000 invoice - 1,000 of credit note actually applied here.
            self.assertAlmostEqual(invoice.amount_residual, 5000.0, places=2)

            payment = self._create_payment(invoice, amount=invoice.amount_residual)
            with self.with_mocked_pac_sign_success():
                invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()

            payment_document = payment.move_id.l10n_mx_edi_payment_document_ids.sorted()[:1]
            self.assertEqual(payment_document.state, "payment_sent")
            tree = etree.fromstring(payment_document.attachment_id.raw)

        # -- No base may ever be zero or negative (#CRP20255). --
        for node in tree.findall(".//pago20:TrasladoDR", PAGO20_NS):
            self.assertGreater(
                float(node.get("BaseDR")),
                0.0,
                f"BaseDR must be greater than zero (SAT #CRP20255), got {node.get('BaseDR')} "
                f"for ImpuestoDR={node.get('ImpuestoDR')} TipoFactorDR={node.get('TipoFactorDR')}.",
            )
        for node in tree.findall(".//pago20:TrasladoP", PAGO20_NS):
            self.assertGreater(float(node.get("BaseP")), 0.0)

        # -- The exempt group was entirely cancelled by the applied part of the
        #    credit note (1,000 invoice base - 1,000 credited), so it is gone. --
        self.assertFalse([
            node
            for node in tree.findall(".//pago20:TrasladoDR", PAGO20_NS)
            if node.get("TipoFactorDR") == "Exento"
        ])

        # -- What remains is the untouched IVA 0% line, not an inflated base. --
        docto_relacionado = tree.find(".//pago20:DoctoRelacionado", PAGO20_NS)
        self.assertAlmostEqual(float(docto_relacionado.get("ImpPagado")), 5000.0, places=2)
        traslado_dr = tree.find(".//pago20:TrasladoDR", PAGO20_NS)
        self.assertAlmostEqual(
            float(traslado_dr.get("BaseDR")),
            5000.0,
            places=2,
            msg="the base must match what is actually being paid, not be scaled up by a "
                "'percentage_paid' above 1.0.",
        )

    def test_payment_complement_no_credit_note_batch_bases_reconcile(self):
        """ End-to-end reproduction using the real data from the rejected
        production CFDI (BBVA1-PBBVA1202605892-MX-Payment-20.xml / invoice
        MT76738, screenshot: MEGAFOL + RADIGROW, both IVA(0%), Total
        $25,073.39, partially paid $14,103.53 alongside a batch of other,
        unrelated, fully-paid invoices - none of them has a credit note).

        For every (impuesto, tasa) group across the whole batch, BaseP must
        equal the sum of the corresponding BaseDR (SAT rule CRP20268).
        """
        older_amounts = [
            5019.354800, 4942.563760, 11025.955168, 2034.408600,
            4483.678100, 8916.020100, 2774.236770, 1793.699700,
            1933.075269, 740.722700, 148.144540, 7973.143953,
            6241.605200, 32577.598392, 8485.161600,
        ]
        mt76738_total = 25073.39
        mt76738_paid = 14103.53

        with freeze_time("2017-01-07"):
            invoices = self.env["account.move"]
            for amt in older_amounts:
                inv = self._create_invoice(
                    invoice_date="2017-01-01",
                    date="2017-01-01",
                    invoice_line_ids=[
                        Command.create({
                            "product_id": self.product.id,
                            "price_unit": amt,
                            "tax_ids": [Command.set(self.tax_0.ids)],
                        }),
                    ],
                )
                with self.with_mocked_pac_sign_success():
                    inv._l10n_mx_edi_cfdi_invoice_try_send()
                invoices |= inv

            mt76738 = self._create_invoice(
                invoice_date="2017-01-06",
                date="2017-01-06",
                invoice_line_ids=[
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 4742.5418,
                        "quantity": 4,
                        "tax_ids": [Command.set(self.tax_0.ids)],
                    }),
                    Command.create({
                        "product_id": self.product.id,
                        "price_unit": 6103.2258,
                        "tax_ids": [Command.set(self.tax_0.ids)],
                    }),
                ],
            )
            with self.with_mocked_pac_sign_success():
                mt76738._l10n_mx_edi_cfdi_invoice_try_send()
            self.assertAlmostEqual(mt76738.amount_total, mt76738_total, places=2)
            invoices |= mt76738

            for inv in invoices:
                self.assertFalse(inv._l10n_mx_edi_get_invoice_related_credit_notes())

            total_amount = sum(older_amounts) + mt76738_paid
            payment = self._create_payment(invoices, amount=total_amount)

            with self.with_mocked_pac_sign_success():
                invoices.l10n_mx_edi_cfdi_invoice_try_update_payments()

            payment_document = payment.move_id.l10n_mx_edi_payment_document_ids.sorted()[:1]
            self.assertEqual(payment_document.state, "payment_sent")
            tree = etree.fromstring(payment_document.attachment_id.raw)

        dr_groups = {}
        for dr in tree.findall(".//pago20:TrasladoDR", PAGO20_NS):
            key = (dr.get("ImpuestoDR"), dr.get("TipoFactorDR"), dr.get("TasaOCuotaDR"))
            dr_groups.setdefault(key, Decimal("0"))
            dr_groups[key] += Decimal(dr.get("BaseDR"))

        p_nodes = tree.findall(".//pago20:TrasladoP", PAGO20_NS)
        self.assertTrue(p_nodes)
        for p_node in p_nodes:
            key = (p_node.get("ImpuestoP"), p_node.get("TipoFactorP"), p_node.get("TasaOCuotaP"))
            self.assertAlmostEqual(
                float(p_node.get("BaseP")),
                float(dr_groups[key]),
                places=6,
                msg=f"BaseP for {key} does not match the sum of its BaseDR (SAT rule CRP20268).",
            )
