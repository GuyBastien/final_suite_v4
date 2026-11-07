# This file is part of the Tryton mobile_payment module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='mobile_payment-tryton',
    version='7.0.2',
    description="Mobile money payments (MonCash, NatCash) as payment "
    "methods of the point of sale",
    long_description=long_description,
    license='AGPL-3',
    author='Pierre Michel Augustin',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1', 'trytond_company>=7.0,<7.1',
        'trytond_currency>=7.0,<7.1', 'trytond_sale_point>=7.0,<7.1',
        'sale_point_quick-tryton', 'segno>=1.5'],
    packages=['trytond.modules.mobile_payment',
        'trytond.modules.mobile_payment.tests'],
    package_dir={
        'trytond.modules.mobile_payment': '.',
        'trytond.modules.mobile_payment.tests': 'tests'},
    package_data={'trytond.modules.mobile_payment': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg']},
    entry_points={
        'trytond.modules': [
            'mobile_payment = trytond.modules.mobile_payment']},
    classifiers=['Framework :: Tryton'],
    )
