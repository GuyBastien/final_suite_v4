# This file is part of the Tryton restaurant_mobile_payment module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='restaurant_mobile_payment-tryton',
    version='7.0.1',
    description='Pay restaurant orders with MonCash or NatCash',
    long_description=long_description,
    license='AGPL-3',
    author='Pierre Michel Augustin',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1',
        'restaurant-tryton>=7.0.1,<7.1',
        'mobile_payment-tryton>=7.0,<7.1'],
    packages=['trytond.modules.restaurant_mobile_payment', 'trytond.modules.restaurant_mobile_payment.tests'],
    package_dir={
        'trytond.modules.restaurant_mobile_payment': '.',
        'trytond.modules.restaurant_mobile_payment.tests': 'tests'},
    package_data={'trytond.modules.restaurant_mobile_payment': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg']},
    entry_points={
        'trytond.modules': [
            'restaurant_mobile_payment = trytond.modules.restaurant_mobile_payment']},
    classifiers=['Framework :: Tryton'],
    )
