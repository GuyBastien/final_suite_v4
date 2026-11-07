# This file is part of the Tryton sale_point_quick module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='sale_point_quick-tryton',
    version='7.0.2',
    description="Faster sales on the point of sale: quick code, favourite products and one-click payments",
    long_description=long_description,
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=['trytond>=7.0,<7.1', 'trytond_sale_point>=7.0,<7.1'],
    packages=['trytond.modules.sale_point_quick',
        'trytond.modules.sale_point_quick.tests'],
    package_dir={
        'trytond.modules.sale_point_quick': '.',
        'trytond.modules.sale_point_quick.tests': 'tests'},
    package_data={'trytond.modules.sale_point_quick': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['sale_point_quick = trytond.modules.sale_point_quick']},
    classifiers=['Framework :: Tryton'],
    )
