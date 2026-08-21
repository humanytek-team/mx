from collections import defaultdict

from odoo import models
from odoo.tools import float_round, frozendict


class AccountMove(models.Model):
    """ Bug fixed here (Odoo 18 Enterprise, l10n_mx_edi):

    'account.move._l10n_mx_edi_add_payment_cfdi_values' prorates the taxes
    of the Payment Complement (CFDI de Pago) using:

        percentage_paid = reconciled_amount / invoice.amount_total

    applied uniformly to the invoice's ORIGINAL tax breakdown (built from
    all its invoice lines, as posted). It never looks at whether a credit
    note already cancelled one of those lines. So when an invoice has two
    lines (e.g. one taxed with IEPS, one IEPS-exempt) and a credit note
    fully refunds the IEPS line, the payment complement generated for the
    remaining balance still shows a proportional slice of the already
    cancelled IEPS tax, which makes 'total' vs the sum of bases/taxes not
    match (SAT complains, and the accounting totals don't reconcile).

    Fix: after Odoo builds the payment complement values, recompute each
    invoice's tax breakdown from its OPEN amount (original amount minus any
    reconciled credit notes, tax group by tax group) instead of the
    original amount, and rebuild the aggregated tax totals from that.

    Only invoices that actually have a related credit note go through this
    recompute; every other invoice is left exactly as Odoo computed it.
    An earlier version of this fix ran unconditionally for every invoice
    and rounded 'base'/'importe' down to currency precision (2 decimals)
    along the way, which silently discarded the 6-decimal precision the
    SAT requires for TrasladoDR/RetencionDR and caused error #CRP20261
    (ImporteDR not matching BaseDR x TasaOCuotaDR within the 0.000001
    tolerance) even for payments with no credit note involved at all.
    """

    _inherit = "account.move"

    # -------------------------------------------------------------------
    # Helpers: what part of the invoice is still open (net of credit notes)
    # -------------------------------------------------------------------

    def _l10n_mx_edi_get_invoice_related_credit_notes(self):
        """ Credit notes reconciled against this invoice's receivable/payable
        line (as opposed to payments). Their tax breakdown must be netted
        out of the invoice's own breakdown before prorating a payment. """
        self.ensure_one()
        pay_rec_lines = self.line_ids.filtered(
            lambda line: line.account_type in ("asset_receivable", "liability_payable")
        )
        credit_notes = self.env["account.move"]
        for field in ("credit", "debit"):
            for partial in pay_rec_lines[f"matched_{field}_ids"]:
                if partial.exchange_move_id:
                    continue
                counterpart_move = partial[f"{field}_move_id"].move_id
                if counterpart_move._l10n_mx_edi_is_cfdi_payment():
                    continue
                if counterpart_move.move_type in ("out_refund", "in_refund"):
                    credit_notes |= counterpart_move
        return credit_notes

    def _l10n_mx_edi_add_open_invoice_cfdi_values(self, cfdi_values):
        """ Same as 'account.move._l10n_mx_edi_add_invoice_cfdi_values' but
        nets out, tax group by tax group, whatever a reconciled credit note
        already cancelled. Only used to compute the Payment Complement's tax
        breakdown, never for the invoice's own CFDI. """
        self.ensure_one()
        document = self.env["l10n_mx_edi.document"]

        self._l10n_mx_edi_add_invoice_cfdi_values(cfdi_values)
        if cfdi_values.get("errors"):
            return

        open_amount_total = self.amount_total

        for credit_note in self._l10n_mx_edi_get_invoice_related_credit_notes():
            cn_values = document._get_company_cfdi_values(credit_note.company_id)
            document._add_certificate_cfdi_values(cn_values)
            credit_note._l10n_mx_edi_add_invoice_cfdi_values(cn_values)
            if cn_values.get("errors"):
                continue

            open_amount_total -= credit_note.amount_total

            for key in ("retenciones_list", "traslados_list", "local_traslados_list", "local_retenciones_list"):
                for cn_tax_values in cn_values.get(key, []):
                    match = next(
                        (
                            tax_values
                            for tax_values in cfdi_values.get(key, [])
                            if tax_values["impuesto"] == cn_tax_values["impuesto"]
                            and tax_values["tipo_factor"] == cn_tax_values["tipo_factor"]
                            and tax_values["tasa_o_cuota"] == cn_tax_values["tasa_o_cuota"]
                        ),
                        None,
                    )
                    if not match:
                        continue
                    for tax_key in ("base", "importe"):
                        if match.get(tax_key) is not None and cn_tax_values.get(tax_key) is not None:
                            match[tax_key] = self.currency_id.round(match[tax_key] - cn_tax_values[tax_key])

        cfdi_values["mx_edi_payment_tax_fix_open_amount_total"] = max(open_amount_total, 0.0)

    # -------------------------------------------------------------------
    # Payment complement: prorate against the OPEN amount, not the
    # invoice's original (pre credit note) amount.
    # -------------------------------------------------------------------

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        # EXTENDS l10n_mx_edi
        res = super()._l10n_mx_edi_add_payment_cfdi_values(cfdi_values, pay_results)

        if cfdi_values.get("errors") or not cfdi_values.get("docto_relationado_list"):
            return res

        document = self.env["l10n_mx_edi.document"]
        any_recomputed = False

        for invoice_values, doc_values in zip(pay_results["invoice_results"], cfdi_values["docto_relationado_list"]):
            invoice = invoice_values["invoice"]

            # Only recompute this invoice's tax breakdown when a credit note
            # actually cancelled part of it. Otherwise, Odoo's own breakdown
            # (already correct, at the 6-decimal precision the SAT expects
            # for this node) is left untouched: this override used to run
            # unconditionally and re-round 'base'/'importe' down to currency
            # precision (2 decimals) even without a credit note involved,
            # which is what triggered SAT error #CRP20261 (ImporteDR not
            # matching BaseDR x TasaOCuotaDR within the 0.000001 tolerance).
            if not invoice._l10n_mx_edi_get_invoice_related_credit_notes():
                continue

            open_inv_cfdi_values = document._get_company_cfdi_values(invoice.company_id)
            document._add_certificate_cfdi_values(open_inv_cfdi_values)
            invoice._l10n_mx_edi_add_open_invoice_cfdi_values(open_inv_cfdi_values)
            if open_inv_cfdi_values.get("errors"):
                continue

            any_recomputed = True

            open_amount_total = open_inv_cfdi_values.get("mx_edi_payment_tax_fix_open_amount_total") or 0.0
            percentage_paid = (
                abs(invoice_values["reconciled_amount"] / open_amount_total) if open_amount_total else 0.0
            )

            for key in ("retenciones_list", "traslados_list", "local_traslados_list", "local_retenciones_list"):
                new_tax_values_list = []
                for tax_values in open_inv_cfdi_values.get(key, []):
                    tax_values = dict(tax_values)
                    for tax_key in ("base", "importe"):
                        if tax_values.get(tax_key) is not None:
                            # Keep the SAT's 6-decimal precision for this node
                            # instead of rounding down to currency precision.
                            tax_values[tax_key] = float_round(tax_values[tax_key] * percentage_paid, precision_digits=6)

                    # A tax fully cancelled by a credit note nets down to a zero base
                    # (Traslado/Retencion) here. The SAT schema requires BaseDR/ImporteDR
                    # to be strictly greater than zero (see error #CRP20255), so such a
                    # tax must be dropped from this payment's breakdown entirely rather
                    # than reported as a zero-valued node.
                    base = tax_values.get("base")
                    importe = tax_values.get("importe")
                    if base is not None and invoice.currency_id.is_zero(base):
                        continue
                    if base is None and importe is not None and invoice.currency_id.is_zero(importe):
                        continue

                    if all(tax_values.get(k) is not None for k in ("base", "importe", "tasa_o_cuota")):
                        post_amounts_map = document._get_post_fix_tax_amounts_map(
                            base_amount=tax_values["base"],
                            tax_amount=tax_values["importe"],
                            tax_rate=tax_values["tasa_o_cuota"],
                            precision_digits=6,
                        )
                        tax_values["importe"] = post_amounts_map["new_tax_amount"]
                        tax_values["base"] = post_amounts_map["new_base_amount"]

                    new_tax_values_list.append(tax_values)
                doc_values[key] = new_tax_values_list

        if any_recomputed:
            self._mx_edi_payment_tax_fix_recompute_totals(cfdi_values)

        return res

    def _mx_edi_payment_tax_fix_recompute_totals(self, cfdi_values):
        """ Rebuilds the aggregated tax lists / SAT totals from the
        (now corrected) per-invoice breakdowns in 'docto_relationado_list'.
        Mirrors the aggregation Odoo performs at the end of
        'account.move._l10n_mx_edi_add_payment_cfdi_values'. """
        self.ensure_one()
        company = cfdi_values["company"]
        company_curr = company.currency_id

        # These keys were already populated (possibly with 'None') by the
        # original computation; clear them so the aggregation below starts
        # from a clean slate instead of accumulating on top of stale values.
        for key in (
            "total_traslados_base_iva0",
            "total_traslados_impuesto_iva0",
            "total_traslados_base_iva_exento",
            "total_traslados_base_iva8",
            "total_traslados_impuesto_iva8",
            "total_traslados_base_iva16",
            "total_traslados_impuesto_iva16",
            "total_retenciones_isr",
            "total_retenciones_iva",
            "total_retenciones_ieps",
        ):
            cfdi_values.pop(key, None)

        def update_tax_amount(key, amount):
            cfdi_values[key] = cfdi_values.get(key, 0.0) + amount

        def check_transferred_tax_values(tax_values, tag, tax_class, amount):
            return (
                tax_values["impuesto"] == tag
                and tax_values["tipo_factor"] == tax_class
                and company_curr.compare_amounts(tax_values["tasa_o_cuota"] or 0.0, amount) == 0
            )

        withholding_values_map = defaultdict(lambda: {"importe": 0.0})
        transferred_values_map = defaultdict(lambda: {"base": 0.0, "importe": 0.0})
        local_retenciones_values_map = defaultdict(lambda: {"base": 0.0, "importe": 0.0})
        local_traslados_values_map = defaultdict(lambda: {"base": 0.0, "importe": 0.0})
        pay_rate = cfdi_values["tipo_cambio"] or 1.0

        for cfdi_inv_values in cfdi_values["docto_relationado_list"]:
            inv_rate = cfdi_inv_values["equivalencia"] or 1.0
            to_mxn_rate = pay_rate / inv_rate

            for result_dict, key in (
                (withholding_values_map, "retenciones_list"),
                (local_retenciones_values_map, "local_retenciones_list"),
            ):
                for tax_values in cfdi_inv_values[key]:
                    tax_key = frozendict({
                        "impuesto": tax_values["impuesto"],
                        "tipo_factor": tax_values["tipo_factor"],
                        "tasa_o_cuota": tax_values["tasa_o_cuota"],
                        "local_tax_name": tax_values.get("local_tax_name"),
                    })
                    result_dict[tax_key]["importe"] += tax_values["importe"] / inv_rate

                    tax_amount_mxn = tax_values["importe"] * to_mxn_rate
                    if tax_values["impuesto"] == "001":
                        update_tax_amount("total_retenciones_isr", tax_amount_mxn)
                    elif tax_values["impuesto"] == "002":
                        update_tax_amount("total_retenciones_iva", tax_amount_mxn)
                    elif tax_values["impuesto"] == "003":
                        update_tax_amount("total_retenciones_ieps", tax_amount_mxn)

            for result_dict, key in (
                (transferred_values_map, "traslados_list"),
                (local_traslados_values_map, "local_traslados_list"),
            ):
                for tax_values in cfdi_inv_values[key]:
                    tax_key = frozendict({
                        "impuesto": tax_values["impuesto"],
                        "tipo_factor": tax_values["tipo_factor"],
                        "tasa_o_cuota": tax_values["tasa_o_cuota"],
                    })
                    tax_amount = tax_values["importe"] or 0.0
                    result_dict[tax_key]["base"] += tax_values["base"] / inv_rate
                    result_dict[tax_key]["importe"] += tax_amount / inv_rate

                    base_amount_mxn = tax_values["base"] * to_mxn_rate
                    tax_amount_mxn = tax_amount * to_mxn_rate
                    if check_transferred_tax_values(tax_values, "002", "Tasa", 0.0):
                        update_tax_amount("total_traslados_base_iva0", base_amount_mxn)
                        update_tax_amount("total_traslados_impuesto_iva0", tax_amount_mxn)
                    elif check_transferred_tax_values(tax_values, "002", "Exento", 0.0):
                        update_tax_amount("total_traslados_base_iva_exento", base_amount_mxn)
                    elif check_transferred_tax_values(tax_values, "002", "Tasa", 0.08):
                        update_tax_amount("total_traslados_base_iva8", base_amount_mxn)
                        update_tax_amount("total_traslados_impuesto_iva8", tax_amount_mxn)
                    elif check_transferred_tax_values(tax_values, "002", "Tasa", 0.16):
                        update_tax_amount("total_traslados_base_iva16", base_amount_mxn)
                        update_tax_amount("total_traslados_impuesto_iva16", tax_amount_mxn)

        for dictionary in (
            withholding_values_map,
            transferred_values_map,
            local_retenciones_values_map,
            local_traslados_values_map,
        ):
            for values in dictionary.values():
                if "base" in values:
                    values["base"] = float_round(values["base"], 6)
                values["importe"] = float_round(values["importe"], 6)

        for key in (
            "total_traslados_base_iva0",
            "total_traslados_impuesto_iva0",
            "total_traslados_base_iva_exento",
            "total_traslados_base_iva8",
            "total_traslados_impuesto_iva8",
            "total_traslados_base_iva16",
            "total_traslados_impuesto_iva16",
            "total_retenciones_isr",
            "total_retenciones_iva",
            "total_retenciones_ieps",
        ):
            if key in cfdi_values:
                cfdi_values[key] = company_curr.round(cfdi_values[key])
            else:
                cfdi_values[key] = None

        for target_key, source_dict in (
            ("retenciones_list", withholding_values_map),
            ("traslados_list", transferred_values_map),
            ("local_retenciones_list", local_retenciones_values_map),
            ("local_traslados_list", local_traslados_values_map),
        ):
            # Same SAT constraint as above (#CRP20255): never emit a node whose
            # base (or, lacking a base, importe) rounds down to zero once
            # aggregated across all the payment's related documents.
            cfdi_values[target_key] = [
                {**k, **v}
                for k, v in source_dict.items()
                if not company_curr.is_zero(v.get("base", v["importe"]))
            ]

        # Cleanup attributes for Exento taxes.
        for key in ("traslados_list", "local_traslados_list"):
            for tax_values in cfdi_values[key]:
                if tax_values["tipo_factor"] == "Exento":
                    tax_values["importe"] = None
