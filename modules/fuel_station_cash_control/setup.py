#!/usr/bin/env python3
from setuptools import setup

name = 'fuel_station_cash_control-tryton'
mod = 'fuel_station_cash_control'

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name=name,
    version='7.0.2',
    description='Banknote and coin count of the fuel station shifts, '
    'based on the denominations of the point of sale',
    long_description=long_description,
    license='GPL-3',
    python_requires='>=3.8',
    package_dir={
        'trytond.modules.' + mod: '.',
        'trytond.modules.' + mod + '.tests': 'tests',
        },
    packages=[
        'trytond.modules.' + mod,
        'trytond.modules.' + mod + '.tests',
        ],
    package_data={
        'trytond.modules.' + mod: [
            'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po'],
        },
    install_requires=[
        'trytond >= 7.0, < 7.1',
        'trytond_company >= 7.0, < 7.1',
        'trytond_currency >= 7.0, < 7.1',
        'fuel_station-tryton >= 7.0.4, < 7.1',
        'sale_point_cash_control-tryton >= 7.0.1, < 7.1',
        ],
    zip_safe=False,
    entry_points={
        'trytond.modules': ['%s = trytond.modules.%s' % (mod, mod)],
        },
    classifiers=['Framework :: Tryton'],
    )
