# This file is part of the Tryton hotel_restaurant module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel_pms.tests.fixture import PmsFixture
from trytond.modules.hotel_restaurant.exceptions import HotelRestaurantError
from trytond.modules.restaurant.tests.test_module import (
    create_item, setup_restaurant)
from trytond.model.modelstorage import DomainValidationError
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class HotelRestaurantTestCase(
        PmsFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test hotel_restaurant module"
    module = 'hotel_restaurant'

    def _world(self, company, check_in=True):
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        hotel, room_type, (room1, room2), party = self._setup(company)
        hotel.room_charge_product = self._service("Room charges", Decimal(0))
        hotel.save()
        stay = self._reservation(company, hotel, party, room_type, room1)
        Reservation.confirm([stay])
        if check_in:
            Reservation.check_in([stay])
        restaurant, t1, t2, _ = setup_restaurant(company, hotel=hotel.id)
        return hotel, stay, restaurant, t1

    def _order(self, restaurant, table, stay, price='100'):
        pool = Pool()
        Order = pool.get('restaurant.order')
        Line = pool.get('restaurant.order.line')
        item = create_item("Griot", price, type='service')
        order, = Order.create([{
                    'restaurant': restaurant.id,
                    'company': restaurant.company.id,
                    'type': 'dine_in', 'table': table.id,
                    'reservation': stay.id if stay else None}])
        Line.create([{
                    'order': order.id, 'product': item.id, 'quantity': 1.0,
                    'unit_price': item.list_price}])
        return Order(order.id)

    @with_transaction()
    def test_room_charge(self):
        "An order paid on the room becomes a charge of the stay"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Reservation = pool.get('hotel.reservation')
            hotel, stay, restaurant, table = self._world(company)
            order = self._order(restaurant, table, stay)
            self.assertEqual(order.hotel, hotel)
            self.assertEqual(order.balance, Decimal('110'))
            Order.send([order])
            payment, = Payment.create([{
                        'order': order.id, 'method': 'room',
                        'amount': order.balance}])
            charge = payment.hotel_charge
            self.assertEqual(charge.reservation, stay)
            self.assertEqual(charge.unit_price, Decimal('110'))
            self.assertIn(order.number, charge.description)
            self.assertEqual(
                Reservation(stay.id).restaurant_orders, (order,))
            # the room charge cannot be edited, only deleted and re-entered
            with self.assertRaises(HotelRestaurantError):
                Payment.write([payment], {'amount': Decimal('50')})

    @with_transaction()
    def test_checkout_blocked_by_open_order(self):
        "A stay cannot be checked out while one of its orders is open"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Reservation = pool.get('hotel.reservation')
            hotel, stay, restaurant, table = self._world(company)
            order = self._order(restaurant, table, stay)
            Order.send([order])
            with self.assertRaises(HotelRestaurantError):
                Reservation.check_out([Reservation(stay.id)])
            Payment.create([{
                        'order': order.id, 'method': 'room',
                        'amount': Order(order.id).balance}])
            Reservation.check_out([Reservation(stay.id)])
            self.assertEqual(Reservation(stay.id).state, 'checked_out')

    @with_transaction()
    def test_room_charge_needs_checked_in_stay(self):
        "No room charge without a checked-in stay"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            hotel, stay, restaurant, table = self._world(
                company, check_in=False)
            order = self._order(restaurant, table, None)
            Order.send([order])
            with self.assertRaises(HotelRestaurantError):
                Payment.create([{
                            'order': order.id, 'method': 'room',
                            'amount': order.balance}])
            # a stay not checked in cannot be chosen on an order either
            with self.assertRaises(DomainValidationError):
                self._order(restaurant, table, stay)

    @with_transaction()
    def test_room_charge_other_hotel(self):
        "An order cannot use the stay of another hotel"
        company = create_company()
        with set_company(company):
            pool = Pool()
            hotel, stay, restaurant, table = self._world(company)
            other, = pool.get('hotel.hotel').create([{
                        'name': "Other", 'code': 'OTH',
                        'company': company.id,
                        'warehouse': hotel.warehouse.id,
                        'consumption_location': hotel.consumption_location.id,
                        }])
            restaurant.hotel = other
            restaurant.save()
            with self.assertRaises(DomainValidationError):
                self._order(restaurant, table, stay)

    @with_transaction()
    def test_charge_removed_with_payment(self):
        "Deleting the payment removes its charge when not billed yet"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Charge = pool.get('hotel.reservation.charge')
            hotel, stay, restaurant, table = self._world(company)
            order = self._order(restaurant, table, stay)
            Order.send([order])
            payment, = Payment.create([{
                        'order': order.id, 'method': 'room',
                        'amount': order.balance}])
            charge_id = payment.hotel_charge.id
            Payment.delete([payment])
            self.assertEqual(Charge.search([('id', '=', charge_id)]), [])

    @with_transaction()
    def test_table_reservation_of_guest(self):
        "Seating a hotel guest's reservation links the order to the stay"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('restaurant.reservation')
            hotel, stay, restaurant, table = self._world(company)
            booking = Reservation(Reservation.create([{
                            'company': company.id,
                            'restaurant': restaurant.id,
                            'hotel_reservation': stay.id,
                            'date': pool.get('ir.date').today(),
                            'time': __import__('datetime').time(20, 0),
                            'persons': 2, 'table': table.id,
                            'guest_name': "Guest"}])[0].id)
            booking.on_change_hotel_reservation()
            self.assertEqual(booking.party, stay.party)


def suite():
    suite = test_tryton.suite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
            HotelRestaurantTestCase))
    return suite
