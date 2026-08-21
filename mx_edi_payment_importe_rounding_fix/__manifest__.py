{
    "name": "MX EDI Payment Complement ImporteDR Rounding Fix",
    "summary": (
        "Fixes CRP20261: recomputes ImporteDR/ImporteP in the Payment Complement "
        "(CFDI de Pago) from BaseDR x TasaOCuotaDR instead of reusing the "
        "accounting amount rounded to cents."
    ),
    "version": "18.0.1.0.0",
    "author": "Humanytek",
    "website": "https://humanytek.com",
    "license": "LGPL-3",
    "category": "Accounting/Localizations",
    "depends": [
        "l10n_mx_edi",
    ],
    "data": [],
    "installable": True,
    "auto_install": False,
}
