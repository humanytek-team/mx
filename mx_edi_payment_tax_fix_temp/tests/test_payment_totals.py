from odoo.tests import TransactionCase, tagged


@tagged("post_install_l10n", "post_install", "-at_install")
class TestPaymentTaxFixTotals(TransactionCase):
    """ Regression test for SAT error #CRP20268 ("BaseP != sum of the
    BaseDR of the related documents").

    Real-world case: invoice FACLN/2026/01899 (SAN24-SAN24202600049-MX-
    Payment-20.xml), a partial payment leaving $0.34 open on a $18590.48
    invoice (percentage_paid ~ 0.99998171). Odoo's native aggregation -
    on the commit this deployment is pinned to on odoo.sh - truncates the
    aggregated 'TrasladoP' base down to currency precision (2 decimals)
    while 'TrasladoDR' keeps the SAT's 6-decimal precision, so BaseP
    (16025.98) ends up strictly lower than BaseDR (16025.986896).

    '_mx_edi_payment_tax_fix_recompute_totals' must rebuild BaseP/ImporteP
    directly from the BaseDR/ImporteDR values so they always match, no
    matter which upstream code path computed the (possibly truncated)
    values it starts from.
    """

    def test_base_p_matches_sum_of_base_dr(self):
        company = self.env.company

        base_dr = 16025.986896
        importe_dr = 2564.157903

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
                        "tasa_o_cuota": 0.16,
                        "base": base_dr,
                        "importe": importe_dr,
                    }],
                },
            ],
        }

        self.env["account.move"].new()._mx_edi_payment_tax_fix_recompute_totals(cfdi_values)

        self.assertEqual(len(cfdi_values["traslados_list"]), 1)
        traslado_p = cfdi_values["traslados_list"][0]
        self.assertEqual(traslado_p["base"], base_dr)
        self.assertEqual(traslado_p["importe"], importe_dr)
        self.assertEqual(cfdi_values["total_traslados_base_iva16"], company.currency_id.round(base_dr))

    def test_aggregates_several_related_documents(self):
        """ Multiple related documents sharing the same tax/rate must be
        summed into a single TrasladoP node whose base is their exact
        sum, not a separately-rounded aggregate. """
        company = self.env.company

        base_dr_values = [14103.531687, 8485.161600, 32577.598392, 6241.605200]

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

        self.assertEqual(len(cfdi_values["traslados_list"]), 1)
        self.assertEqual(cfdi_values["traslados_list"][0]["base"], round(sum(base_dr_values), 6))
