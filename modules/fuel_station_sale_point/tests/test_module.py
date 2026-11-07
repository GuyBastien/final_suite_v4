# This file is part of the Tryton fuel_station_sale_point module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.fuel_station.tests.test_module import (
    FuelStationTestCase)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class FuelSalePointTestCase(CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test fuel_station_sale_point module"
    module = 'fuel_station_sale_point'

    _setup = FuelStationTestCase._setup
    _shift = FuelStationTestCase._shift

    @with_transaction()
    def test_shift_without_point(self):
        "A station without point of sale shows no shop ticket"
        company = create_company()
        with set_company(company):
            ctx = self._setup(company)
            shift = self._shift(company, ctx)
            self.assertEqual(shift.pos_sale_count, 0)
            self.assertEqual(shift.pos_sale_total, Decimal(0))

    @with_transaction()
    def test_shift_with_point(self):
        "Done tickets of the point of sale on the shift date are counted"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Point = pool.get('sale.point')
            Sale = pool.get('sale.point.sale')
            Line = pool.get('sale.point.sale.line')
            Location = pool.get('stock.location')
            Journal = pool.get('account.journal')
            journal, = Journal.create([{
                        'name': "POS", 'code': 'POS', 'type': 'revenue'}])
            ModelData = pool.get('ir.model.data')
            Strict = pool.get('ir.sequence.strict')
            sequence, = Strict.create([{
                        'name': "POS", 'sequence_type': ModelData.get_id(
                            'sale_point', 'sequence_type_sale'),
                        'company': company.id}])
            ctx = self._setup(company)
            station = ctx['station']
            storage = station.warehouse.storage_location
            customer, = Location.search([('type', '=', 'customer')], limit=1)
            point, = Point.create([{
                        'name': "Shop", 'company': company.id,
                        'storage_location': storage.id,
                        'return_location': storage.id,
                        'customer_location': customer.id,
                        'journal': journal.id, 'sequence': sequence.id}])
            station.sale_point = point
            station.save()
            shift = self._shift(company, ctx)
            sale, other = Sale.create([
                    {'company': company.id, 'point': point.id,
                        'date': shift.date},
                    {'company': company.id, 'point': point.id,
                        'date': shift.date}])
            Sale.write([sale], {'number': "T1"})
            Template = pool.get('product.template')
            Uom = pool.get('product.uom')
            unit, = Uom.search([('name', '=', "Unit")])
            template, = Template.create([{
                        'name': "Water", 'type': 'goods',
                        'default_uom': unit.id, 'sale_uom': unit.id,
                        'salable': True, 'list_price': Decimal('10'),
                        'products': [('create', [{}])]}])
            Line.create([{
                        'sale': sale.id, 'product': template.products[0].id,
                        'quantity': 2.0,
                        'unit_list_price': Decimal('0'),
                        'unit_gross_price': Decimal('0')}])
            Sale.write([sale], {'state': 'done'})
            self.assertEqual(shift.pos_sale_count, 1)
            # a done ticket must be fully paid: a free ticket keeps the test short
            self.assertEqual(shift.pos_sale_total, Decimal('0.00'))


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            FuelSalePointTestCase))
    return suite
