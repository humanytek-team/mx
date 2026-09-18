{
    "name": "MX EDI Payment Tax Fix (TEMPORAL - quitar al actualizar enterprise en odoo.sh)",
    "version": "19.0.1.1.0",
    "category": "Accounting/Localizations",
    "summary": "TEMPORAL: Fix BaseP/ImporteP vs BaseDR/ImporteDR on the CFDI Payment Complement (CRP20268) until odoo.sh's enterprise commit is updated",
    "description": """
TEMPORAL - desinstalar y eliminar en cuanto la rama `enterprise` de este
proyecto en odoo.sh incluya el commit `63e9a296312e43de98b9943d38b9eaf96e53ea44`
("[REV] l10n_mx_edi: payment complement amounts in currency decimals") o uno
posterior. Ver ``KNOWN_BUGS.md`` para el commit exacto al que está pineado
este ambiente y el análisis completo.

Odoo's ``account.move._l10n_mx_edi_add_payment_cfdi_values`` computes the
per-document breakdown (``TrasladoDR``/``RetencionDR``) and the aggregated
payment totals (``TrasladoP``/``RetencionP``) via two independent code paths.
On the specific commit this instance is pinned to on odoo.sh, the aggregate
is additionally truncated down to currency precision, which the SAT rejects
with error ``CRP20268`` ("BaseP != sum of the BaseDR of the related
documents").

This module rebuilds ``TrasladoP``/``RetencionP`` (and the ``Totales`` node)
directly from the already-rendered ``TrasladoDR``/``RetencionDR`` entries, so
the two always agree by construction, regardless of the upstream code path.
Once odoo.sh's `enterprise` commit is updated past the fix, this module
becomes redundant (harmless, but redundant) and should be removed.
""",
    "author": "Humanytek",
    "depends": ["l10n_mx_edi"],
    "data": [
        "views/payment20_precision_fix.xml",
    ],
    "installable": True,
    "license": "LGPL-3",
}
