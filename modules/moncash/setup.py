# This file is part of the Tryton moncash module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='moncash-tryton',
    version='7.0.2',
    description="MonCash (Digicel Haiti) payments for the point of sale",
    long_description=long_description,
    license='GPL-3',
    author='Pierre Michel Augustin',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1', 'mobile_payment-tryton'],
    packages=['trytond.modules.moncash', 'trytond.modules.moncash.tests'],
    package_dir={
        'trytond.modules.moncash': '.',
        'trytond.modules.moncash.tests': 'tests'},
    package_data={'trytond.modules.moncash': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg']},
    entry_points={
        'trytond.modules': ['moncash = trytond.modules.moncash']},
    classifiers=['Framework :: Tryton'],
    )
