# This file is part of the Tryton hotel_sale_point module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='hotel_sale_point-tryton',
    version='7.0.1',
    description="One-click room charge on the quick point of sale",
    long_description=long_description,
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1', 'hotel_pms-tryton', 'sale_point_quick-tryton',
        'trytond_sale_point>=7.0,<7.1'],
    packages=['trytond.modules.hotel_sale_point',
        'trytond.modules.hotel_sale_point.tests'],
    package_dir={
        'trytond.modules.hotel_sale_point': '.',
        'trytond.modules.hotel_sale_point.tests': 'tests'},
    package_data={'trytond.modules.hotel_sale_point': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['hotel_sale_point = trytond.modules.hotel_sale_point']},
    classifiers=['Framework :: Tryton'],
    )
