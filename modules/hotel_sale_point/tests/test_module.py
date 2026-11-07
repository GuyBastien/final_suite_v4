# This file is part of the Tryton hotel_sale_point module.
import unittest
from decimal import Decimal

from trytond.modules.account.tests import get_fiscalyear
from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel_pms.exceptions import ReservationError
from trytond.modules.hotel_pms.tests.fixture import PmsFixture
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class HotelSalePointTestCase(
        PmsFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test hotel_sale_point module"
    module = 'hotel_sale_point'

    @with_transaction()
    def test_pay_room(self):
        "Test the one-click room charge"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Account = pool.get('account.account')
            Category = pool.get('product.category')
            Method = pool.get('sale.point.payment.method')
            POS = pool.get('sale.point')
            POSSale = pool.get('sale.point.sale')
            Hotel = pool.get('hotel.hotel')
            hotel, room_type, (room1, room2), party = self._setup(company)
            pool.get('party.address').create([{'party': party.id}])
            fiscalyear = get_fiscalyear(company)
            fiscalyear.save()
            fiscalyear.create_period([fiscalyear])
            revenue, = Account.search([
                    ('type.revenue', '=', True),
                    ('company', '=', company.id)], limit=1)
            expense, = Account.search([
                    ('type.expense', '=', True),
                    ('company', '=', company.id)], limit=1)
            clearing, = Account.create([{
                        'name': "Room charges clearing",
                        'parent': revenue.parent.id,
                        'type': revenue.type.id, 'company': company.id}])
            category, = Category.create([{
                        'name': "Clearing", 'accounting': True,
                        'account_revenue': clearing.id,
                        'account_expense': expense.id}])
            Hotel.write([hotel], {
                    'room_charge_product': self._service(
                        "Room charges", Decimal(0), category=category.id).id})
            sequence_type_id = pool.get('ir.model.data').get_id(
                'sale_point', 'sequence_type_sale')
            sequence, = pool.get('ir.sequence.strict').create([{
                        'name': "POS", 'company': company.id,
                        'sequence_type': sequence_type_id}])
            journal, = pool.get('account.journal').search(
                [('type', '=', 'revenue')], limit=1)
            point, = POS.create([{
                        'name': "Bar", 'company': company.id,
                        'sequence': sequence.id,
                        'storage_location': hotel.warehouse.storage_location.id,
                        'customer_location': hotel.consumption_location.id,
                        'journal': journal.id}])
            drink = self._service("Cocktail", Decimal('12'))
            today = pool.get('ir.date').today()

            def sale_for(reservation):
                return POSSale.create([{
                            'company': company.id, 'point': point.id,
                            'date': today,
                            'reservation': (
                                reservation.id if reservation else None),
                            'lines': [('create', [{
                                            'product': drink.id,
                                            'quantity': 2.,
                                            'unit_list_price': Decimal('12'),
                                            'unit_gross_price': Decimal('12'),
                                            }])]}])[0]

            # no reservation selected
            with self.assertRaises(ReservationError):
                POSSale.pay_room([sale_for(None)])
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Reservation.check_in([reservation])
            # no room charge payment method yet
            with self.assertRaises(ReservationError):
                POSSale.pay_room([sale_for(reservation)])
            method, = Method.create([{
                        'name': "Room", 'company': company.id,
                        'account': clearing.id, 'room_charge': True}])
            sale = sale_for(reservation)
            POSSale.pay_room([sale])
            sale = POSSale(sale.id)
            payment, = sale.payments
            self.assertEqual(payment.method, method)
            self.assertEqual(payment.amount, Decimal('24.00'))
            self.assertEqual(sale.state, 'done')


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            HotelSalePointTestCase))
    return suite
