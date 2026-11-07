# This file is part of the Tryton natcash module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='natcash-tryton',
    version='7.0.2',
    description="NatCash (Natcom Haiti) payments for the point of sale",
    long_description=long_description,
    license='AGPL-3',
    author='Pierre Michel Augustin',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1', 'mobile_payment-tryton'],
    extras_require={'sdk': ['natcashify>=0.1']},
    packages=['trytond.modules.natcash', 'trytond.modules.natcash.tests'],
    package_dir={
        'trytond.modules.natcash': '.',
        'trytond.modules.natcash.tests': 'tests'},
    package_data={'trytond.modules.natcash': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg']},
    entry_points={
        'trytond.modules': ['natcash = trytond.modules.natcash']},
    classifiers=['Framework :: Tryton'],
    )
