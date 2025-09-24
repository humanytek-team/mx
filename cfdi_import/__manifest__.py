{
    "name": "CFDI Import",
    "version": "1.1",  # for Odoo V18+
    "author": "Humanytek",
    "website": "https://humanytek.com",
    "depends": [
        "l10n_mx_edi",
        "account_invoice_extract",
    ],
    "data": [
        # security
        "security/ir.model.access.csv",
        # data
        # reports
        # views
        "views/res_partner.xml",
        # wizards
        "wizards/cfdi_importer.xml",
    ],
    "external_dependencies": {
        "python": [
            "xmltodict",  # 0.13.0
        ],
    },
    "application": True,
    "license": "LGPL-3",
}
