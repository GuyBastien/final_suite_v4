# This file is part of the Tryton party_person_ht module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='party_person_ht-tryton',
    version='7.0.3',
    description="Tryton module for Haiti: Haitian Creole language, "
    "first name of persons, NIF and NINU identifiers, arrondissement "
    "and communal section subdivisions",
    long_description=long_description,
    license='GPL-3',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1', 'trytond_country>=7.0,<7.1',
        'trytond_party>=7.0,<7.1'],
    packages=['trytond.modules.party_person_ht',
        'trytond.modules.party_person_ht.tests'],
    package_dir={
        'trytond.modules.party_person_ht': '.',
        'trytond.modules.party_person_ht.tests': 'tests'},
    package_data={'trytond.modules.party_person_ht': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po']},
    entry_points={
        'trytond.modules': ['party_person_ht = '
            'trytond.modules.party_person_ht']},
    classifiers=['Framework :: Tryton'],
    )
