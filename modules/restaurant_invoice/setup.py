# This file is part of the Tryton restaurant_invoice module.
from setuptools import setup

setup(
    name='restaurant_invoice-tryton',
    version='7.0.2',
    description="Tryton module to invoice the orders of restaurants",
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=['trytond>=7.0,<7.1', 'restaurant-tryton>=7.0',
        'trytond_account_invoice>=7.0,<7.1'],
    packages=['trytond.modules.restaurant_invoice',
        'trytond.modules.restaurant_invoice.tests'],
    package_dir={
        'trytond.modules.restaurant_invoice': '.',
        'trytond.modules.restaurant_invoice.tests': 'tests'},
    package_data={'trytond.modules.restaurant_invoice': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['restaurant_invoice = '
            'trytond.modules.restaurant_invoice']},
    )
