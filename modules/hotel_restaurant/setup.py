# This file is part of the Tryton hotel_restaurant module.
from setuptools import setup

try:
    with open('README.rst', encoding='utf-8') as f:
        long_description = f.read()
except OSError:
    long_description = ''

setup(
    name='hotel_restaurant-tryton',
    version='7.0.1',
    description='Hotel and restaurant link: room charge and shared tables',
    long_description=long_description,
    license='GPL-3',
    author='Pierre Michel Augustin',
    python_requires='>=3.8',
    install_requires=[
        'trytond>=7.0,<7.1',
        'hotel_services-tryton>=7.0.1,<7.1',
        'hotel_pms-tryton>=7.0.1,<7.1',
        'restaurant-tryton>=7.0.1,<7.1'],
    packages=['trytond.modules.hotel_restaurant', 'trytond.modules.hotel_restaurant.tests'],
    package_dir={
        'trytond.modules.hotel_restaurant': '.',
        'trytond.modules.hotel_restaurant.tests': 'tests'},
    package_data={'trytond.modules.hotel_restaurant': [
        'tryton.cfg', '*.xml', 'view/*.xml', 'locale/*.po', 'icons/*.svg']},
    entry_points={
        'trytond.modules': [
            'hotel_restaurant = trytond.modules.hotel_restaurant']},
    classifiers=['Framework :: Tryton'],
    )
