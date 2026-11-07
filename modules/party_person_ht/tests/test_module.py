# This file is part of the Tryton party_person_ht module.
import unittest

from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction

from ..exceptions import InvalidHaitiIdentifier
from ..party import format_nif, is_valid_ninu, is_valid_nif


class PartyPersonHtTestCase(test_tryton.ModuleTestCase):
    "Test party_person_ht module"
    module = 'party_person_ht'

    def test_nif_ninu_validators(self):
        "Validators accept separators and reject wrong lengths"
        self.assertTrue(is_valid_nif('000-123-456-7'))
        self.assertTrue(is_valid_nif('0001234567'))
        self.assertFalse(is_valid_nif('123456789'))
        self.assertFalse(is_valid_nif('12345678AB'))
        self.assertEqual(format_nif('0001234567'), '000-123-456-7')
        self.assertTrue(is_valid_ninu('1234567890'))
        self.assertTrue(is_valid_ninu('12345678901234567'))
        self.assertFalse(is_valid_ninu('123456789'))
        self.assertFalse(is_valid_ninu('123456789012345678'))

    @with_transaction()
    def test_language(self):
        "Haitian Creole is a translatable language"
        Lang = Pool().get('ir.lang')
        lang, = Lang.search([('code', '=', 'ht')])
        self.assertEqual(lang.name, "Haitian Creole")
        self.assertTrue(lang.translatable)
        self.assertEqual(lang.date, '%d/%m/%Y')

    @with_transaction()
    def test_identifier_types_in_configuration(self):
        "NIF and NINU are available as identifier types"
        Configuration = Pool().get('party.configuration')
        types = dict(Configuration(1).get_identifier_types())
        self.assertIn('ht_nif', types)
        self.assertIn('ht_ninu', types)
        Identifier = Pool().get('party.identifier')
        self.assertIn('ht_nif', dict(Identifier.get_types()))

    @with_transaction()
    def test_identifiers(self):
        "NIF and NINU are validated and compacted"
        pool = Pool()
        Party = pool.get('party.party')
        Identifier = pool.get('party.identifier')
        party, = Party.create([{'name': "Jean"}])
        ident, = Identifier.create([{
                    'party': party.id, 'type': 'ht_nif',
                    'code': '0001234567'}])
        self.assertEqual(party.tax_identifier, ident)
        with self.assertRaises(InvalidHaitiIdentifier):
            Identifier.create([{
                        'party': party.id, 'type': 'ht_nif',
                        'code': '123'}])
        Identifier.create([{
                    'party': party.id, 'type': 'ht_ninu',
                    'code': '1234567890'}])
        with self.assertRaises(InvalidHaitiIdentifier):
            Identifier.create([{
                        'party': party.id, 'type': 'ht_ninu',
                        'code': 'ABC'}])
        ident = Identifier(type='ht_nif', code='000-123-456-7')
        self.assertEqual(ident.on_change_with_code(), '0001234567')

    @with_transaction()
    def test_party_first_name(self):
        "First name is part of the name and searchable"
        Party = Pool().get('party.party')
        person, company = Party.create([
                {'name': "Pierre", 'is_person': True, 'first_name': "Marie"},
                {'name': "Pierre SA", 'is_person': False,
                    'first_name': "ignored"}])
        self.assertEqual(person.rec_name, "Marie Pierre")
        self.assertEqual(company.rec_name, "Pierre SA")
        self.assertEqual(person.full_name, "Marie Pierre")
        self.assertEqual(company.full_name, "Pierre SA")
        found = Party.search([('rec_name', 'ilike', 'Marie%')])
        self.assertEqual(found, [person])
        person.is_person = False
        person.on_change_is_person()
        self.assertIsNone(person.first_name)

    @with_transaction()
    def test_subdivision_types(self):
        "Arrondissement and communal section are subdivision types"
        pool = Pool()
        Country = pool.get('country.country')
        Subdivision = pool.get('country.subdivision')
        types = dict(Subdivision.fields_get(['type'])['type']['selection'])
        self.assertIn('arrondissement', types)
        self.assertIn('communal section', types)
        haiti, = Country.create([{'name': "Haiti", 'code': 'HT'}]) \
            if not Country.search([('code', '=', 'HT')]) \
            else Country.search([('code', '=', 'HT')])
        dep, = Subdivision.create([{
                    'country': haiti.id, 'name': "Ouest",
                    'code': 'HT-OU', 'type': 'department'}])
        arr, = Subdivision.create([{
                    'country': haiti.id, 'name': "Port-au-Prince",
                    'code': 'HT-OU-PP', 'type': 'arrondissement',
                    'parent': dep.id}])
        sec, = Subdivision.create([{
                    'country': haiti.id, 'name': "Turgeau",
                    'code': 'HT-OU-PP-1', 'type': 'communal section',
                    'parent': arr.id}])
        self.assertEqual(sec.parent.parent, dep)


class PartyPersonHtCreoleTestCase(test_tryton.ModuleTestCase):
    "Test the Haitian Creole translation file"
    module = 'party_person_ht'

    @with_transaction()
    def test_creole_labels(self):
        "Labels of the module and of party come in Haitian Creole"
        import os
        pool = Pool()
        Party = pool.get('party.party')
        Lang = pool.get('ir.lang')
        Translation = pool.get('ir.translation')
        lang, = Lang.search([('code', '=', 'ht')])
        self.assertTrue(lang.translatable)
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'locale', 'ht.po')
        Translation.translation_import('ht', 'party_person_ht', [path])
        with Transaction().set_context(language='ht'):
            fields = Party.fields_get(['first_name', 'name'])
            self.assertEqual(fields['first_name']['string'], 'Prenon')
            self.assertEqual(fields['name']['string'], 'Non')


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            PartyPersonHtTestCase))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            PartyPersonHtCreoleTestCase))
    return suite
