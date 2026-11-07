# This file is part of the Tryton fuel_station_invoice module.
from setuptools import find_packages, setup

setup(
    name='fuel_station_invoice-tryton',
    version='7.0.2',
    description="Tryton module to invoice the credit sales of fuel stations",
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=['trytond>=7.0,<7.1', 'fuel_station-tryton>=7.0'],
    packages=['trytond.modules.fuel_station_invoice',
        'trytond.modules.fuel_station_invoice.tests'],
    package_dir={
        'trytond.modules.fuel_station_invoice': '.',
        'trytond.modules.fuel_station_invoice.tests': 'tests'},
    package_data={'trytond.modules.fuel_station_invoice': [
        'tryton.cfg', '*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['fuel_station_invoice = '
            'trytond.modules.fuel_station_invoice']},
    )
