from odoo import api, models
from odoo.tools import float_is_zero, float_round


class AccountMove(models.Model):
    """ Bug fixed here (Odoo 18 Enterprise, l10n_mx_edi) -- SAT error CRP20261:

    'account.move._l10n_mx_edi_add_payment_cfdi_values' fills 'importe' (the
    ImporteDR/ImporteP attributes of the Payment Complement's Traslado/
    Retencion nodes) from the invoice's ACCOUNTING tax amount, already
    rounded to cents (e.g. 104.27), instead of recomputing
    'BaseDR x TasaOCuotaDR' with 6-decimal precision (e.g.
    1737.88 x 0.06 = 104.2728). The SAT rejects the CFDI when both values
    differ by more than 0.000001, which the cents-rounded accounting amount
    routinely does.

    Fix: after Odoo builds the payment complement values, recompute
    'importe' for every Traslado/Retencion node (both per-document and the
    aggregated totals) from its own 'base' and 'tasa_o_cuota', at 6-decimal
    precision, so it always falls within the SAT's tolerance. Left untouched
    are: 'Exento' nodes (no 'importe' attribute) and nodes with no rate
    (e.g. a zero-rate VAT), which don't need a recompute.
    """

    _inherit = "account.move"

    @api.model
    def _l10n_mx_edi_payment_importe_rounding_fix_recompute(self, tax_values):
        # Use 'raw_base' (full float precision, before it gets displayed
        # rounded to 6 decimals into 'base') so the recompute matches what
        # the SAT/PAC actually validates. Multiplying the already-rounded
        # 'base' by the rate can be off by a few millionths and fail the
        # very tolerance this fix is meant to satisfy.
        raw_base = tax_values.get("raw_base", tax_values.get("base"))
        rate = tax_values.get("tasa_o_cuota")
        importe = tax_values.get("importe")
        if raw_base is None or rate is None or importe is None:
            return
        if float_is_zero(rate, precision_digits=6):
            return
        tax_values["importe"] = float_round(raw_base * rate, precision_digits=6)

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        # EXTENDS l10n_mx_edi
        res = super()._l10n_mx_edi_add_payment_cfdi_values(cfdi_values, pay_results)

        if cfdi_values.get("errors"):
            return res

        target_lists = ("retenciones_list", "traslados_list", "local_retenciones_list", "local_traslados_list")

        # Per-document (DR) nodes.
        for doc_values in cfdi_values.get("docto_relationado_list") or []:
            for target_list in target_lists:
                for tax_values in doc_values.get(target_list) or []:
                    self._l10n_mx_edi_payment_importe_rounding_fix_recompute(tax_values)

        # Aggregated (P) nodes.
        for target_list in target_lists:
            for tax_values in cfdi_values.get(target_list) or []:
                self._l10n_mx_edi_payment_importe_rounding_fix_recompute(tax_values)

        return res
