MX EDI Payment Complement Tax Proration Fix
============================================

Problema
--------

En una factura con dos o más líneas donde alguna tiene IEPS y otra está
exenta, si se genera una nota de crédito que reembolsa **totalmente** una de
las líneas, el Complemento de Pago (CFDI de Pago) generado para el saldo
restante sigue prorrateando el IEPS de la línea ya cancelada por la nota de
crédito. El total del complemento no cuadra contra la suma de bases +
impuestos.

Causa raíz
----------

En ``l10n_mx_edi/models/account_move.py``, el método
``_l10n_mx_edi_add_payment_cfdi_values`` calcula::

    percentage_paid = reconciled_amount / invoice.amount_total

y aplica ese porcentaje de forma pareja a **toda** la lista de impuestos
original de la factura (``traslados_list``, ``retenciones_list``, etc.),
construida a partir de todas las líneas de la factura tal como se
registraron. El cálculo nunca considera que una nota de crédito ya canceló
una de esas líneas: ``invoice.amount_total`` sigue siendo el total original
y la lista de impuestos sigue incluyendo el IEPS del producto ya
reembolsado.

Solución
--------

Este módulo:

1. Agrega ``account.move._l10n_mx_edi_get_invoice_related_credit_notes()``
   para identificar las notas de crédito conciliadas contra la factura.
2. Agrega ``account.move._l10n_mx_edi_add_open_invoice_cfdi_values()``, que
   construye el mismo desglose de impuestos que Odoo pero le resta, grupo
   de impuesto por grupo de impuesto, lo ya cubierto por esas notas de
   crédito, y calcula el monto "abierto" real de la factura
   (``amount_total`` menos el total de las notas de crédito).
3. Extiende ``_l10n_mx_edi_add_payment_cfdi_values`` para, después de que
   Odoo construya el complemento (dejando intactos fecha, tipo de cambio,
   datos bancarios, etc.), recalcular el porcentaje pagado contra el monto
   **abierto** y reconstruir el desglose de impuestos de cada documento
   relacionado a partir de esa lista neta. Finalmente reconstruye los
   totales agregados del complemento (``retenciones_list``,
   ``traslados_list``, ``total_traslados_impuesto_iva16``, etc.) a partir
   de los desgloses corregidos.

Con esto, si la nota de crédito canceló por completo la línea con IEPS, ese
impuesto queda en $0 en el complemento de pago del saldo restante, y el
total vuelve a cuadrar.

Limitación conocida
--------------------

El cálculo neta **todas** las notas de crédito conciliadas contra la
factura al momento de generar el complemento, sin importar el orden
cronológico frente a otros pagos ya tímbrados anteriormente. Para el caso
típico (una nota de crédito y un pago restante) esto es correcto. Si existen
varios pagos parciales intercalados cronológicamente con varias notas de
crédito, revisar caso por caso.

Instalación
-----------

Solo depende de ``l10n_mx_edi`` (Enterprise). No requiere configuración
adicional; el ajuste aplica automáticamente al generar/timbrar el
Complemento de Pago.
