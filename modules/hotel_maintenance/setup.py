#!/usr/bin/env python3
from setuptools import setup

name = 'hotel_maintenance-tryton'
mod = 'hotel_maintenance'

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name=name,
    version='7.0.2',
    description='Hotel maintenance: work orders, preventive schedules, lost and found, laundry',
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
        'trytond.modules.' + mod: ['tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg'],
        },
    install_requires=[
        'trytond >= 7.0, < 7.1',
        'trytond_company >= 7.0, < 7.1',
        'trytond_party >= 7.0, < 7.1',
        'trytond_product >= 7.0, < 7.1',
        'trytond_stock >= 7.0, < 7.1',
        'hotel-tryton >= 7.0, < 7.1',
        'hotel_pms-tryton >= 7.0, < 7.1',
        ],
    zip_safe=False,
    entry_points={
        'trytond.modules': ['%s = trytond.modules.%s' % (mod, mod)],
        },
    classifiers=['Framework :: Tryton'],
    )
