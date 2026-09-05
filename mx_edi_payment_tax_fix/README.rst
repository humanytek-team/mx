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

Solo se recalcula el desglose de una factura si tiene realmente una nota de
crédito conciliada; cualquier otra factura queda exactamente como Odoo la
calculó, sin tocar nada.

Bug corregido (CRP20261)
-------------------------

Una versión anterior de este módulo ejecutaba el recálculo para **toda**
factura, tuviera o no nota de crédito, y redondeaba ``base``/``importe`` a
la precisión de la moneda (2 decimales) en el proceso. Eso descartaba en
silencio la precisión de 6 decimales que el SAT exige para
``TrasladoDR``/``RetencionDR``, provocando el error ``CRP20261``
(``ImporteDR`` no coincide con ``BaseDR × TasaOCuotaDR`` dentro de la
tolerancia de 0.000001) incluso en pagos sin ninguna nota de crédito
involucrada. Se corrigió para que el módulo solo actúe cuando de verdad hay
una nota de crédito que netear, y para que use precisión de 6 decimales
(no 2) al hacerlo.

Bug corregido (CRP20268)
-------------------------

Odoo nativo calcula el desglose por documento (``TrasladoDR``/``RetencionDR``)
y los totales agregados del pago (``TrasladoP``/``RetencionP``) con **dos
sumas independientes**: el desglose DR se redondea a 6 decimales por
factura, mientras que los totales P se suman a partir de los montos
"crudos" (sin redondear) de las líneas base de **todas** las facturas del
pago, y solo se redondea una vez, al final. Esas dos sumas, calculadas por
caminos distintos, pueden discrepar en una micro-unidad por simple ruido de
punto flotante — sin que exista ninguna nota de crédito de por medio. Esto
fue justo lo que rechazó un pago real de 17 facturas
(``BBVA1-PBBVA1202605892-MX-Payment-20.xml``): ``BaseP="113352.470196"``
contra la suma correcta de los ``BaseDR``, ``113352.470195`` — exactamente
el desajuste que la regla ``CRP20268`` del SAT rechaza (``BaseP`` debe ser
igual a la suma de las bases de los documentos relacionados cuyo impuesto y
tasa coincidan con los de ese nodo) —, aunque **ninguna** de esas 17
facturas tenía nota de crédito.

La causa de fondo era que el recálculo de totales de este módulo
(``_mx_edi_payment_tax_fix_recompute_totals``) solo se ejecutaba cuando
**alguna** factura del pago tenía una nota de crédito de por medio
(``any_recomputed``); si ninguna la tenía, el lote completo se quedaba con
los totales nativos de Odoo, calculados por ese segundo camino
independiente y propenso a esta discrepancia. Por eso, aplicar únicamente
el primer arreglo de este módulo no resolvía el error en pagos sin notas de
crédito. Se corrigió para que el recálculo de totales se ejecute
**siempre** que haya documentos relacionados, con o sin nota de crédito:
así los totales ``TrasladoP``/``RetencionP`` siempre se reconstruyen a
partir del mismo desglose por documento que ya se muestra en
``TrasladoDR``/``RetencionDR``, garantizando que ambos cuadren.

Bug corregido (CRP20255)
-------------------------

Al netear una nota de crédito, el módulo restaba su desglose de impuestos y
su ``amount_total`` **completos**, aunque esa nota de crédito estuviera
repartida entre varias facturas y solo una parte se hubiera conciliado
contra ésta. Sobre-restar tiene dos efectos, y ambos aparecieron juntos en
un CFDI real rechazado (``BBVA1-PBBVA1202605893-MX-Payment-20.xml``):

1. La base de un grupo de impuestos se va **por debajo de cero**
   (``BaseDR="-2971.890000"`` en el nodo IEPS Exento), que es exactamente lo
   que rechaza la regla ``CRP20255`` del SAT ("El valor del campo BaseDR ...
   debe ser mayor que cero").
2. ``open_amount_total`` queda más chico que el saldo realmente abierto, con
   lo que ``percentage_paid`` se pasa de 1.0 e **infla todas las bases**: en
   ese mismo XML, ``BaseDR="43121.360000"`` contra un ``ImpPagado`` de solo
   ``19437.15`` (2.2 veces más).

Se corrigió para netear solo la **porción de cada nota de crédito realmente
conciliada contra esta factura** (``_l10n_mx_edi_get_invoice_credit_notes_amounts``
devuelve ese monto por nota de crédito), escalando su desglose por
``monto aplicado / total de la nota``. Además se agregaron tres candados:
el neteo nunca deja una base por debajo de cero, ``percentage_paid`` se
limita a 1.0, y si al netear no queda nada abierto la factura se deja tal
como la calculó Odoo en vez de inventar números.

Esto también elimina la limitación que este README documentaba antes (el
neteo ya no ignora cuánto de la nota de crédito corresponde a esta factura).

Limitación conocida
--------------------

El cálculo neta las notas de crédito conciliadas contra la factura al
momento de generar el complemento, sin importar el orden cronológico frente
a otros pagos ya timbrados anteriormente. Para el caso típico (notas de
crédito aplicadas antes de los pagos) esto es correcto. Si existen varios
pagos parciales intercalados cronológicamente con varias notas de crédito,
revisar caso por caso.

Instalación
-----------

Solo depende de ``l10n_mx_edi`` (Enterprise). No requiere configuración
adicional; el ajuste aplica automáticamente al generar/timbrar el
Complemento de Pago.
