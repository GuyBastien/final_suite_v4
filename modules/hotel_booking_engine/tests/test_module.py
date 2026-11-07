# This file is part of the Tryton hotel_booking_engine module.
import datetime
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from .. import web
from ..exceptions import BookingError, BookingUnavailableError

ARRIVAL = datetime.date.today() + datetime.timedelta(days=10)
DEPARTURE = ARRIVAL + datetime.timedelta(days=2)
GUEST = {
    'first_name': "Ann", 'last_name': "Guest", 'email': "Ann@Example.com",
    'phone': "+509 1111 2222"}


class BookingEngineTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_booking_engine module"
    module = 'hotel_booking_engine'

    def _setup(self, company, rooms=('101', '102'), **settings):
        pool = Pool()
        Room = pool.get('hotel.room')
        Hotel = pool.get('hotel.hotel')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        Room.create([{
                    'hotel': hotel.id, 'name': n, 'type': room_type.id}
                for n in rooms])
        values = {'booking_engine_enabled': True}
        values.update(settings)
        Hotel.write([hotel], values)
        return Hotel(hotel.id), room_type

    def test_web_parsers(self):
        "Test the public pages parse their inputs defensively"
        self.assertEqual(web._date("2030-01-02"), datetime.date(2030, 1, 2))
        self.assertIsNone(web._date("02/01/2030"))
        self.assertIsNone(web._date(None))
        self.assertEqual(web._int("3", 1), 3)
        self.assertEqual(web._int("x", 1), 1)
        self.assertEqual(web._int(None, 7), 7)

    def test_wants_json(self):
        "The mobile application asks for JSON through the Accept header"
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        for accept, expected in [
                ('application/json', True), ('text/html', False),
                ('*/*', False), (None, False)]:
            headers = {'Accept': accept} if accept else {}
            request = Request(EnvironBuilder(headers=headers).get_environ())
            self.assertEqual(web._wants_json(request), expected, accept)

    @with_transaction()
    def test_token_and_publication(self):
        "Test enabling the booking creates the secret address"
        company = create_company()
        with set_company(company):
            Hotel = Pool().get('hotel.hotel')
            hotel = create_hotel(company)
            self.assertFalse(hotel.booking_token)
            self.assertIsNone(Hotel.booking_by_token(None))
            Hotel.write([hotel], {'booking_engine_enabled': True})
            hotel = Hotel(hotel.id)
            token = hotel.booking_token
            self.assertTrue(len(token) >= 20)
            self.assertIn(token, hotel.booking_url)
            self.assertEqual(Hotel.booking_by_token(token), hotel)
            self.assertIsNone(Hotel.booking_by_token('wrong'))
            Hotel.new_booking_token([hotel])
            self.assertNotEqual(Hotel(hotel.id).booking_token, token)
            self.assertIsNone(Hotel.booking_by_token(token))
            # A disabled hotel is not published
            Hotel.write([hotel], {'booking_engine_enabled': False})
            self.assertIsNone(
                Hotel.booking_by_token(Hotel(hotel.id).booking_token))

    @with_transaction()
    def test_search(self):
        "Test the search lists the available room types with prices"
        company = create_company()
        with set_company(company):
            hotel, room_type = self._setup(company)
            offers = hotel.booking_search(ARRIVAL, DEPARTURE, 2, 0, 1)
            offer, = offers
            self.assertEqual(offer['code'], 'DBL')
            self.assertEqual(offer['available'], 2)
            self.assertEqual(offer['nights'], 2)
            self.assertEqual(offer['night_price'], Decimal('100'))
            self.assertEqual(offer['total'], Decimal('200'))
            # Two rooms cost twice
            offer, = hotel.booking_search(ARRIVAL, DEPARTURE, 2, 0, 2)
            self.assertEqual(offer['total'], Decimal('400'))
            # Not enough capacity for 5 guests in one double room
            self.assertEqual(
                hotel.booking_search(ARRIVAL, DEPARTURE, 5, 0, 1), [])
            # No more rooms than available
            self.assertEqual(
                hotel.booking_search(ARRIVAL, DEPARTURE, 2, 0, 3), [])

    @with_transaction()
    def test_search_validations(self):
        "Test the requests are validated"
        company = create_company()
        with set_company(company):
            hotel, room_type = self._setup(
                company, booking_max_nights=5, booking_max_rooms=2)
            for args in [
                    (DEPARTURE, ARRIVAL, 2, 0, 1),
                    (ARRIVAL, ARRIVAL + datetime.timedelta(days=6), 2, 0, 1),
                    (datetime.date.today() - datetime.timedelta(days=3),
                        datetime.date.today() - datetime.timedelta(days=1),
                        2, 0, 1),
                    (ARRIVAL, DEPARTURE, 0, 0, 1),
                    (ARRIVAL, DEPARTURE, 2, -1, 1),
                    (ARRIVAL, DEPARTURE, 2, 0, 0),
                    (ARRIVAL, DEPARTURE, 2, 0, 3),
                    (ARRIVAL + datetime.timedelta(days=900),
                        ARRIVAL + datetime.timedelta(days=902), 2, 0, 1),
                    ("2030-01-01", "2030-01-03", 2, 0, 1),
                    ]:
                with self.assertRaises(BookingError, msg=str(args)):
                    hotel.booking_search(*args)

    @with_transaction()
    def test_reserve_draft(self):
        "Test the online reservation is a draft for the front desk"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Notification = pool.get('hotel.notification')
            Party = pool.get('party.party')
            hotel, room_type = self._setup(company)
            reservation = hotel.booking_reserve(
                ARRIVAL, DEPARTURE, 2, 0, {room_type.id: 1}, GUEST,
                notes="Late arrival")
            self.assertEqual(reservation.state, 'draft')
            self.assertEqual(reservation.source, 'web')
            self.assertEqual(reservation.total_amount, Decimal('200.00'))
            self.assertEqual(reservation.party.rec_name, "Ann Guest")
            self.assertIn("Late arrival", reservation.notes)
            line, = reservation.lines
            self.assertIsNone(line.room)
            self.assertEqual(line.unit_price, Decimal('100'))
            self.assertIsNone(reservation.deposit_requested)
            notification, = Notification.search([])
            self.assertEqual(notification.kind, 'info')
            self.assertIn(reservation.number, notification.title)
            # The e-mail identifies the guest: no duplicate party
            second = hotel.booking_reserve(
                ARRIVAL + datetime.timedelta(days=20),
                DEPARTURE + datetime.timedelta(days=20), 1, 0,
                {room_type.id: 1}, dict(GUEST, email="ann@example.com"))
            self.assertEqual(second.party, reservation.party)
            self.assertEqual(len(Party.search([('name', '=', "Guest")])), 1)

    @with_transaction()
    def test_reserve_auto_confirm_and_deposit(self):
        "Test automatic confirmation assigns the rooms; deposit is computed"
        company = create_company()
        with set_company(company):
            hotel, room_type = self._setup(
                company, booking_auto_confirm=True,
                booking_deposit_rate=Decimal('0.3'))
            reservation = hotel.booking_reserve(
                ARRIVAL, DEPARTURE, 3, 1, {room_type.id: 2}, GUEST)
            self.assertEqual(reservation.state, 'confirmed')
            self.assertEqual(len(reservation.lines), 2)
            self.assertEqual(
                len({l.room.id for l in reservation.lines}), 2)
            self.assertEqual(reservation.total_amount, Decimal('400.00'))
            self.assertEqual(reservation.deposit_requested, Decimal('120.00'))
            # The hotel is now full for these dates
            with self.assertRaises(BookingUnavailableError):
                hotel.booking_reserve(
                    ARRIVAL, DEPARTURE, 2, 0, {room_type.id: 1},
                    dict(GUEST, email="bob@example.com"))
            self.assertEqual(
                hotel.booking_search(ARRIVAL, DEPARTURE, 2, 0, 1), [])

    @with_transaction()
    def test_reserve_validations(self):
        "Test invalid guests and room types are refused"
        company = create_company()
        with set_company(company):
            hotel, room_type = self._setup(company)
            rooms = {room_type.id: 1}
            for guest in [
                    dict(GUEST, email="not-an-email"),
                    dict(GUEST, email=""),
                    dict(GUEST, last_name=""),
                    dict(GUEST, last_name="x" * 61),
                    dict(GUEST, phone=12345),
                    ]:
                with self.assertRaises(BookingError, msg=str(guest)):
                    hotel.booking_reserve(
                        ARRIVAL, DEPARTURE, 2, 0, rooms, guest)
            for bad in [{}, None, {9999: 1}, {room_type.id: -1},
                    {"abc": 1}]:
                with self.assertRaises(BookingError, msg=str(bad)):
                    hotel.booking_reserve(
                        ARRIVAL, DEPARTURE, 2, 0, bad, GUEST)
            # Capacity: 5 guests do not fit in one double room
            with self.assertRaises(BookingError):
                hotel.booking_reserve(
                    ARRIVAL, DEPARTURE, 5, 0, rooms, GUEST)
            with self.assertRaises(BookingError):
                hotel.booking_reserve(
                    ARRIVAL, DEPARTURE, 2, 0, rooms, GUEST,
                    notes="n" * 501)
