# Bugs conocidos que resuelve `mx_edi_payment_tax_fix_temp` (Odoo 19)

**Módulo:** `mx_edi_payment_tax_fix_temp` 19.0.1.0.0 · **Depende de:** `l10n_mx_edi` (Enterprise)
**Alcance:** Complemento de Pago (CFDI de Pago 2.0)

> ⚠️ **Temporal.** Este módulo es un parche mientras la rama `enterprise` de
> este proyecto en odoo.sh sigue pineada al commit `c874ba3aab5...`. En
> cuanto se actualice a un commit que incluya `63e9a296312e43de98b9943d38b9eaf96e53ea44`
> ("[REV] l10n_mx_edi: payment complement amounts in currency decimals") o
> posterior, este módulo queda redundante (inofensivo, pero de más) y debe
> desinstalarse y eliminarse del repo.

Todo lo que este módulo toca vive en `account.move._l10n_mx_edi_add_payment_cfdi_values`
(`l10n_mx_edi/models/account_move.py`, Odoo 19 Enterprise).

## Caso real

`SAN24-SAN24202600049-MX-Payment-20.xml` — pago parcial de la factura
`FACLN/2026/01899` (18590.48 de total, 18590.14 pagados, 0.34 queda abierto,
`percentage_paid ≈ 0.99998171`). Rechazado por el SAT:

```
Código: CRP20268
Mensaje: El campo BaseP que corresponde a Traslado, no es igual a la suma de
los importes de las bases registrados en los documentos relacionados...
Información adicional: Traslados: La sumatoria de BaseDR es mayor a BaseP
16025.98. Valores minimos permitidos: 16025.986896
```

## Causa raíz

`l10n_mx_edi` calcula el desglose por documento (`TrasladoDR`/`RetencionDR`)
y el agregado del pago (`TrasladoP`/`RetencionP`) por **dos caminos
independientes**. Cuál es exactamente el bug de ese segundo camino depende
del commit de `enterprise` que tenga cada instancia — se han visto (y
revertido entre sí) al menos dos variantes upstream en la misma semana:

- Redondeo de 6 decimales en un lado y una segunda suma independiente sobre
  datos con ruido de punto flotante en el otro (discrepancias de
  microunidad).
- (El commit al que está pineada esta instancia en odoo.sh,
  `enterprise@c874ba3aab5...`, que incluye `03acaca0e` "payment complement
  amounts in currency decimals" pero no su reversión `63e9a2963`): el
  agregado se **trunca** (`rounding_method='DOWN'`) a la precisión de la
  moneda (2 decimales para MXN) mientras `TrasladoDR` se queda en 6
  decimales, así que `BaseP < Σ BaseDR` en prácticamente cualquier pago
  parcial.

Con la factura del caso real: `raw_base = 16025.986896` (el mismo valor ya
redondeado que se muestra en `BaseDR`). El camino nativo de `TrasladoP` hace
`float_round(16025.986896, precision_digits=2, rounding_method='DOWN')` =
`16025.98`, en vez de `16025.99` (redondeo normal) o `16025.986896` (sin
truncar) — de ahí el rechazo.

## Cómo lo resuelve el módulo

`_mx_edi_payment_tax_fix_recompute_totals()` reconstruye los nodos
`TrasladoP`/`RetencionP` (y el nodo `Totales`) **sumando directamente los
valores ya puestos en `TrasladoDR`/`RetencionDR`** (agrupando por
`ImpuestoDR`+`TasaOCuotaDR`), sin pasar por el segundo camino de cálculo de
Odoo. Así `BaseP == Σ BaseDR` queda garantizado por construcción,
independientemente de qué variante del bug tenga el commit de `enterprise`
desplegado en cada ambiente.

**Tests:** `test_base_p_matches_sum_of_base_dr`,
`test_aggregates_several_related_documents`

## Referencia

Para el mismo bug en Odoo 18, ver `mx/mx_edi_payment_tax_fix` (rama `18.0`
de este mismo repositorio) — `_mx_edi_payment_tax_fix_recompute_totals` es
prácticamente idéntico ahí; este módulo es un port a v19 con el mismo
enfoque, sin las correcciones de notas de crédito (bugs 1/3/4/5 de la v18),
que en v19 no se han reproducido todavía. A diferencia de la v18, este SÍ es
temporal: en v18 el bug es nativo y sigue sin corregirse upstream; en v19 ya
existe el fix (`63e9a2963`), solo falta que odoo.sh actualice el commit de
`enterprise`.
