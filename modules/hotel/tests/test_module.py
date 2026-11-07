# This file is part of the Tryton hotel module.
import datetime
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import ConsumptionError, RateError


def create_hotel(company):
    pool = Pool()
    Location = pool.get('stock.location')
    Hotel = pool.get('hotel.hotel')
    customer, = Location.create([{'name': "Customer", 'type': 'customer'}])
    input_location, output_location, storage_location = Location.create([
            {'name': "Input", 'type': 'storage'},
            {'name': "Output", 'type': 'storage'},
            {'name': "Storage", 'type': 'storage'},
            ])
    warehouse, = Location.create([{
                'name': "Warehouse", 'code': "WH", 'type': 'warehouse',
                'input_location': input_location.id,
                'output_location': output_location.id,
                'storage_location': storage_location.id,
                }])
    hotel, = Hotel.create([{
                'name': "Grand Hotel",
                'code': "GH",
                'company': company.id,
                'warehouse': warehouse.id,
                'consumption_location': customer.id,
                }])
    return hotel


def create_room_type(hotel, **rate):
    pool = Pool()
    RoomType = pool.get('hotel.room.type')
    Rate = pool.get('hotel.rate')
    room_type, = RoomType.create([{
                'hotel': hotel.id, 'name': "Double", 'code': "DBL",
                'capacity': 2}])
    values = {
        'room_type': room_type.id, 'name': "Standard",
        'night_price': Decimal('100'), 'hour_price': Decimal('20'),
        'min_hours': 2, 'max_hours': 8,
        }
    values.update(rate)
    Rate.create([values])
    return RoomType(room_type.id)


class HotelTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel module"
    module = 'hotel'

    @with_transaction()
    def test_party_first_name(self):
        "Test the first name of a person in the party name"
        Party = Pool().get('party.party')
        person, org = Party.create([
                {'name': "Dupont", 'first_name': "Marie", 'is_person': True},
                {'name': "Acme", 'first_name': "Ignored"}])
        self.assertEqual(person.rec_name, "Marie Dupont")
        self.assertEqual(person.full_name, "Marie Dupont")
        self.assertEqual(org.rec_name, "Acme")
        self.assertEqual(Party.search([('rec_name', 'ilike', 'Marie%')]), [
                person])
        self.assertEqual(Party.search([('rec_name', 'ilike', 'Dupont')]), [
                person])
        self.assertFalse(Party.default_is_person())

    @with_transaction()
    def test_hotel_defaults(self):
        "Test the hotel gets the company currency"
        company = create_company()
        with set_company(company):
            hotel = create_hotel(company)
            self.assertEqual(hotel.currency, company.currency)
            self.assertEqual(hotel.check_in_time, datetime.time(14, 0))

    @with_transaction()
    def test_rate_nightly(self):
        "Test nightly price with weekend supplement"
        company = create_company()
        with set_company(company):
            room_type = create_room_type(
                create_hotel(company), weekend_night_price=Decimal('150'))
            # 2024-01-04 is a Thursday: Thu (100) + Fri (150) + Sat (150)
            start = datetime.datetime(2024, 1, 4, 14, 0)
            end = datetime.datetime(2024, 1, 7, 11, 0)
            price = room_type.compute_price(start, end)
            self.assertEqual(price['mode'], 'nightly')
            self.assertEqual(price['quantity'], Decimal(3))
            self.assertEqual(price['amount'], Decimal('400.00'))
            self.assertEqual(price['unit_price'], Decimal('133.33'))

    @with_transaction()
    def test_rate_hourly(self):
        "Test hourly price with minimum hours and night cap"
        company = create_company()
        with set_company(company):
            room_type = create_room_type(create_hotel(company))
            start = datetime.datetime(2024, 1, 2, 10, 0)
            # 1h is billed 2h (minimum): 2 x 20
            price = room_type.compute_price(
                start, start + datetime.timedelta(hours=1))
            self.assertEqual(price['mode'], 'hourly')
            self.assertEqual(price['quantity'], Decimal(2))
            self.assertEqual(price['amount'], Decimal('40.00'))
            # 3h20 is billed 4h: 4 x 20
            price = room_type.compute_price(
                start, start + datetime.timedelta(hours=3, minutes=20))
            self.assertEqual(price['quantity'], Decimal(4))
            self.assertEqual(price['amount'], Decimal('80.00'))
            # Beyond max_hours the nightly price applies
            price = room_type.compute_price(
                start, start + datetime.timedelta(hours=9))
            self.assertEqual(price['mode'], 'nightly')
            self.assertEqual(price['amount'], Decimal('100.00'))

    @with_transaction()
    def test_rate_hourly_capped_to_night(self):
        "Test an hourly stay never costs more than a night"
        company = create_company()
        with set_company(company):
            room_type = create_room_type(
                create_hotel(company),
                hour_price=Decimal('30'), max_hours=12)
            start = datetime.datetime(2024, 1, 2, 8, 0)
            price = room_type.compute_price(
                start, start + datetime.timedelta(hours=10), mode='hourly')
            self.assertEqual(price['mode'], 'hourly')
            self.assertEqual(price['amount'], Decimal('100.00'))

    @with_transaction()
    def test_rate_errors(self):
        "Test pricing errors"
        company = create_company()
        with set_company(company):
            hotel = create_hotel(company)
            room_type = create_room_type(hotel, hour_price=None)
            start = datetime.datetime(2024, 1, 2, 8, 0)
            with self.assertRaises(RateError):
                room_type.compute_price(start, start)
            with self.assertRaises(RateError):
                room_type.compute_price(
                    start, start + datetime.timedelta(hours=2),
                    mode='hourly')
            with self.assertRaises(RateError):
                # No rate valid for this date
                room_type.rates[0].end_date = datetime.date(2023, 12, 31)
                room_type.rates[0].save()
                room_type.compute_price(
                    start, start + datetime.timedelta(days=1))

    @with_transaction()
    def test_rate_seasons(self):
        "Test the most specific season rate is selected"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Rate = pool.get('hotel.rate')
            room_type = create_room_type(create_hotel(company))
            Rate.create([{
                        'room_type': room_type.id, 'name': "Summer",
                        'start_date': datetime.date(2024, 7, 1),
                        'end_date': datetime.date(2024, 8, 31),
                        'night_price': Decimal('180'),
                        }])
            start = datetime.datetime(2024, 7, 10, 14, 0)
            price = room_type.compute_price(
                start, start + datetime.timedelta(days=2))
            self.assertEqual(price['amount'], Decimal('360.00'))
            start = datetime.datetime(2024, 9, 10, 14, 0)
            price = room_type.compute_price(
                start, start + datetime.timedelta(days=2))
            self.assertEqual(price['amount'], Decimal('200.00'))

    @with_transaction()
    def test_room_location(self):
        "Test a stock location is created for each room"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Room = pool.get('hotel.room')
            hotel = create_hotel(company)
            room_type = create_room_type(hotel)
            room, = Room.create([{
                        'hotel': hotel.id, 'name': "101",
                        'type': room_type.id}])
            self.assertEqual(room.state, 'available')
            self.assertEqual(room.location.name, "GH/101")
            self.assertEqual(
                room.location.parent, hotel.warehouse.storage_location)
            Room.make_dirty([room])
            self.assertEqual(room.state, 'dirty')

    def _create_article(self, **values):
        pool = Pool()
        Template = pool.get('product.template')
        Uom = pool.get('product.uom')
        unit, = Uom.search([('name', '=', "Unit")])
        template_values = {
            'name': "Water bottle",
            'type': 'goods',
            'default_uom': unit.id,
            'list_price': Decimal('3'),
            'hotel_article': True,
            'hotel_category': 'minibar',
            'hotel_billable': True,
            'products': [('create', [{'cost_price': Decimal('1')}])],
            }
        template_values.update(values)
        template, = Template.create([template_values])
        return template.products[0]

    @with_transaction()
    def test_consumption_stock_moves(self):
        "Test replenishment and consumption generate stock moves"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Room = pool.get('hotel.room')
            Operation = pool.get('hotel.consumption')
            Move = pool.get('stock.move')
            Location = pool.get('stock.location')
            Product = pool.get('product.product')

            hotel = create_hotel(company)
            room_type = create_room_type(hotel)
            room, = Room.create([{
                        'hotel': hotel.id, 'name': "101",
                        'type': room_type.id}])
            product = self._create_article()
            unit = product.default_uom
            today = Pool().get('ir.date').today()

            # Put 10 bottles in the warehouse
            supplier, = Location.create([{'name': "S", 'type': 'supplier'}])
            receipt, = Move.create([{
                        'product': product.id, 'unit': unit.id,
                        'quantity': 10.,
                        'from_location': supplier.id,
                        'to_location': hotel.warehouse.storage_location.id,
                        'company': company.id,
                        'currency': company.currency.id,
                        'unit_price': Decimal('1'),
                        'effective_date': today,
                        }])
            Move.do([receipt])

            def quantity(location, with_childs=False):
                "Stock of the product in the location (and its children)"
                with Transaction().set_context(stock_date_end=today):
                    quantities = Product.products_by_location(
                        [location.id], with_childs=with_childs,
                        grouping_filter=([product.id],))
                return quantities.get((location.id, product.id), 0.)

            # Replenish the minibar of the room with 6 bottles
            replenishment, = Operation.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'kind': 'replenishment', 'room': room.id,
                        'date': today,
                        'lines': [('create', [{
                                        'product': product.id,
                                        'quantity': 6., 'unit': unit.id,
                                        }])],
                        }])
            self.assertTrue(replenishment.number)
            Operation.process([replenishment])
            self.assertEqual(replenishment.state, 'done')
            storage = hotel.warehouse.storage_location
            self.assertEqual(quantity(room.location), 6.)
            self.assertEqual(quantity(storage), 4.)
            # Room stock is still part of the warehouse stock
            self.assertEqual(quantity(storage, with_childs=True), 10.)

            # The guest drinks 2 bottles (billable)
            consumption, = Operation.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'kind': 'consumption', 'room': room.id,
                        'date': today,
                        'lines': [('create', [{
                                        'product': product.id,
                                        'quantity': 2., 'unit': unit.id,
                                        'billable': True,
                                        'unit_price': Decimal('3'),
                                        }])],
                        }])
            Operation.process([consumption])
            self.assertEqual(quantity(room.location), 4.)
            self.assertEqual(quantity(hotel.consumption_location), 2.)
            self.assertEqual(quantity(storage, with_childs=True), 8.)
            line, = consumption.lines
            self.assertEqual(line.move.state, 'done')
            self.assertEqual(line.move.origin, line)

            with self.assertRaises(ConsumptionError):
                Operation.delete([consumption])

    @with_transaction()
    def test_consumption_validations(self):
        "Test operations without lines or room location are refused"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Operation = pool.get('hotel.consumption')
            hotel = create_hotel(company)
            today = pool.get('ir.date').today()
            empty, = Operation.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'kind': 'consumption', 'date': today}])
            with self.assertRaises(ConsumptionError):
                Operation.process([empty])


del ModuleTestCase
