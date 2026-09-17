from collections import defaultdict

from odoo import models
from odoo.tools import float_round, frozendict


class AccountMove(models.Model):
    """ Bug fixed here (Odoo 19 Enterprise, l10n_mx_edi):

    'account.move._l10n_mx_edi_add_payment_cfdi_values' computes the
    per-document breakdown (TrasladoDR/RetencionDR) and the aggregated
    payment totals (TrasladoP/RetencionP) through two independent code
    paths. Depending on the exact upstream commit deployed, those two
    paths can disagree - see KNOWN_BUGS.md for the specific rejected CFDI
    this was written against (SAT error #CRP20268: "BaseP != sum of the
    BaseDR of the related documents sharing the same tax/rate").

    On the commit this instance is pinned to on odoo.sh, the aggregated
    'TrasladoP'/'RetencionP' amounts are additionally truncated down to
    currency precision (2 decimals for MXN) while 'TrasladoDR'/
    'RetencionDR' stay at the SAT's required 6 decimals, which guarantees
    BaseP < sum(BaseDR) whenever there is any fractional remainder beyond
    2 decimals - i.e. on virtually every partial payment.

    Fix: always rebuild the aggregated totals (TrasladoP/RetencionP, and
    the 'Totales' node) from the already-rendered per-document breakdown
    (TrasladoDR/RetencionDR) instead of trusting Odoo's own second
    aggregation. This guarantees BaseP == sum(BaseDR) by construction,
    independent of which upstream commit is actually deployed.
    """

    _inherit = "account.move"

    def _l10n_mx_edi_add_payment_cfdi_values(self, cfdi_values, pay_results):
        # EXTENDS l10n_mx_edi
        res = super()._l10n_mx_edi_add_payment_cfdi_values(cfdi_values, pay_results)

        if cfdi_values.get("errors") or not cfdi_values.get("docto_relationado_list"):
            return res

        self._mx_edi_payment_tax_fix_recompute_totals(cfdi_values)

        return res

    def _mx_edi_payment_tax_fix_recompute_totals(self, cfdi_values):
        """ Rebuilds the aggregated tax lists / SAT totals from the
        per-document breakdowns in 'docto_relationado_list' (i.e. from
        what is actually rendered as TrasladoDR/RetencionDR), instead of
        trusting Odoo's own separate aggregation. Mirrors the shape of
        the aggregation Odoo performs natively, so the rendering template
        does not need to change. """
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
            # Every key in these maps comes from a node that IS present in some
            # related document, so each one must get its aggregated counterpart:
            # dropping a group here while its TrasladoDR/RetencionDR still exists
            # is exactly the BaseP-vs-sum(BaseDR) mismatch of #CRP20268. A group
            # whose aggregated base rounds down to zero keeps the same 0.000001
            # floor Odoo itself applies, so the node stays valid for #CRP20255.
            for values in source_dict.values():
                if "base" in values:
                    values["base"] = values["base"] or 0.000001
            cfdi_values[target_key] = [{**k, **v} for k, v in source_dict.items()]

        # Cleanup attributes for Exento taxes.
        for key in ("traslados_list", "local_traslados_list"):
            for tax_values in cfdi_values[key]:
                if tax_values["tipo_factor"] == "Exento":
                    tax_values["importe"] = None
