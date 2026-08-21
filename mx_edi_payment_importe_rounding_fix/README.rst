MX EDI Payment Complement ImporteDR Rounding Fix
=================================================

Problema
--------

Al timbrar un Recibo Electrónico de Pago (REP / CFDI tipo "P"), el PAC
rechaza el XML con el error ``CRP20261`` porque el atributo ``ImporteDR`` de
un nodo ``TrasladoDR`` o ``RetencionDR`` (por ejemplo IEPS al 6%) no
coincide, dentro de la tolerancia de 6 decimales que exige el SAT, con el
producto ``BaseDR × TasaOCuotaDR``. Por ejemplo::

    <pago20:TrasladoDR BaseDR="1737.880000" ImporteDR="104.270000" ImpuestoDR="003" TipoFactorDR="Tasa" TasaOCuotaDR="0.060000"/>

El SAT espera ``1737.88 × 0.06 = 104.2728`` (tolerancia ±0.000001), pero
Odoo generó ``104.27``.

Causa raíz
----------

En ``l10n_mx_edi/models/account_move.py``, el método
``_l10n_mx_edi_add_payment_cfdi_values`` reutiliza el monto de impuesto ya
contabilizado en la factura (redondeado a 2 decimales, como exige toda
póliza contable) para llenar ``importe`` de los nodos del Complemento de
Pago, en lugar de recalcular ``base × tasa`` con la precisión de 6
decimales que el SAT exige para este nodo. Cuando la combinación de
base/centavos hace que esa diferencia supere ``0.000001``, el PAC rechaza
el CFDI.

No es un problema de configuración de decimales ni del método de redondeo
de impuestos de la compañía: el monto manual (``manual_tax_amounts``)
intercepta el cálculo antes de que esos ajustes tengan efecto.

Solución
--------

Este módulo extiende ``account.move._l10n_mx_edi_add_payment_cfdi_values``
para que, después de que Odoo construya el Complemento de Pago, recalcule
``importe`` de cada nodo Traslado/Retención (tanto a nivel de documento
relacionado -DR- como en los totales agregados -P-) como
``base × tasa_o_cuota`` con precisión de 6 decimales, en vez del monto
contable redondeado a centavos. Los nodos ``Exento`` (sin atributo
``importe``) y las tasas en cero quedan sin tocar.

Instalación
-----------

Solo depende de ``l10n_mx_edi`` (Enterprise). No requiere configuración
adicional; el ajuste aplica automáticamente al generar/timbrar el
Complemento de Pago.

Nota
----

Si además se instala ``mx_edi_payment_tax_fix`` (que resuelve un problema
distinto: proración de impuestos cuando una nota de crédito ya canceló
parte de la factura), verificar el orden de aplicación, ya que ese módulo
redondea explícitamente a la precisión de la moneda (2 decimales) al final
de su propio recálculo.
