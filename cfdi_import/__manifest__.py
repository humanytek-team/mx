{
    "name": "CFDI Import",
    "version": "17.0.1.0.1",
    "author": "Humanytek",
    "website": "https://humanytek.com",
    "depends": [
        "l10n_mx_edi",
    ],
    "data": [
        # security
        "security/ir.model.access.csv",
        # data
        # reports
        # views
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
