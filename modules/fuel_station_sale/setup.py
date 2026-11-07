# This file is part of the Tryton fuel_station_sale module.
from setuptools import setup

setup(
    name='fuel_station_sale-tryton',
    version='7.0.2',
    description="Tryton module creating sales and invoices from fuel "
    "dispensing authorizations",
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=['trytond>=7.0,<7.1', 'fuel_station-tryton>=7.0.3',
        'trytond_sale>=7.0,<7.1'],
    packages=['trytond.modules.fuel_station_sale',
        'trytond.modules.fuel_station_sale.tests'],
    package_dir={
        'trytond.modules.fuel_station_sale': '.',
        'trytond.modules.fuel_station_sale.tests': 'tests'},
    package_data={'trytond.modules.fuel_station_sale': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['fuel_station_sale = '
            'trytond.modules.fuel_station_sale']},
    )
