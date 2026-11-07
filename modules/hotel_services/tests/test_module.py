# This file is part of the Tryton hotel_services module.
import datetime
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel_pms.tests.helpers import (
    make_reservation, service, setup_hotel)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from ..exceptions import ServiceError

TODAY = datetime.date.today()
START = datetime.datetime.combine(TODAY, datetime.time(0, 0))
END = START + datetime.timedelta(days=2)


class ServicesTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_services module"
    module = 'hotel_services'

    def _services(self, company, **values):
        pool = Pool()
        Service = pool.get('hotel.service')
        Resource = pool.get('hotel.service.resource')
        hotel, room_type, (room1, room2), party = setup_hotel(company)
        massage, = Service.create([dict({
                        'hotel': hotel.id, 'name': "Massage",
                        'category': 'spa', 'duration': 60, 'capacity': 1,
                        'product': service("Massage", '80').id},
                    **values)])
        therapist, = Resource.create([{'hotel': hotel.id, 'name': "Anna"}])
        return hotel, room_type, room1, party, massage, therapist

    def _booking(self, hotel, party, svc, resource=None, hour=10, **values):
        Booking = Pool().get('hotel.service.booking')
        data = {
            'hotel': hotel.id, 'service': svc.id, 'party': party.id,
            'date': TODAY, 'start_time': datetime.time(hour, 0),
            'duration': 60, 'unit_price': Decimal('80'),
            'participants': 1}
        if resource:
            data['resource'] = resource.id
        data.update(values)
        return Booking.create([data])[0]

    @with_transaction()
    def test_resource_busy(self):
        "Test a resource cannot be double booked"
        company = create_company()
        with set_company(company):
            Booking = Pool().get('hotel.service.booking')
            hotel, _, _, party, massage, anna = self._services(company)
            first = self._booking(hotel, party, massage, anna)
            Booking.confirm([first])
            second = self._booking(
                hotel, party, massage, anna, hour=10)
            with self.assertRaises(ServiceError):
                Booking.confirm([second])
            third = self._booking(
                hotel, party, massage, anna, hour=11)
            Booking.confirm([third])
            self.assertEqual(third.state, 'confirmed')

    @with_transaction()
    def test_capacity(self):
        "Test the capacity of a service is enforced per slot"
        company = create_company()
        with set_company(company):
            Booking = Pool().get('hotel.service.booking')
            hotel, _, _, party, yoga, _ = self._services(
                company, name="Yoga", category='activity', capacity=3)
            Booking.confirm([self._booking(
                        hotel, party, yoga, participants=2)])
            with self.assertRaises(ServiceError):
                Booking.confirm([self._booking(
                            hotel, party, yoga, participants=2)])
            Booking.confirm([self._booking(
                        hotel, party, yoga, participants=1)])

    @with_transaction()
    def test_charge_to_reservation(self):
        "Test completing a booking adds a charge to the reservation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Booking = pool.get('hotel.service.booking')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party, massage, _ = self._services(
                company, capacity=2)
            reservation = make_reservation(
                company, hotel, party, room_type, room, start=START,
                end=END)
            Reservation.confirm([reservation])
            booking = self._booking(
                hotel, party, massage, reservation=reservation.id,
                participants=2)
            Booking.confirm([booking])
            Booking.complete([booking])
            self.assertEqual(booking.state, 'done')
            self.assertIsNotNone(booking.charge)
            self.assertEqual(booking.charge.reservation, reservation)
            self.assertEqual(booking.charge.amount, Decimal('160.00'))
            with self.assertRaises(ServiceError):
                Booking.delete([booking])

    @with_transaction()
    def test_sale_without_reservation(self):
        "Test completing a booking without reservation creates a sale"
        company = create_company()
        with set_company(company):
            Booking = Pool().get('hotel.service.booking')
            hotel, _, _, party, massage, _ = self._services(company)
            booking = self._booking(hotel, party, massage)
            Booking.confirm([booking])
            Booking.complete([booking])
            self.assertIsNone(booking.charge)
            self.assertIsNotNone(booking.sale)
            self.assertEqual(booking.sale.party, party)
            self.assertEqual(len(booking.sale.lines), 1)

    @with_transaction()
    def test_workflow_and_end_time(self):
        "Test cancel, reset and the computed end time"
        company = create_company()
        with set_company(company):
            Booking = Pool().get('hotel.service.booking')
            hotel, _, _, party, massage, _ = self._services(company)
            booking = self._booking(hotel, party, massage, hour=23)
            self.assertEqual(booking.end_time, datetime.time(0, 0))
            Booking.cancel([booking])
            Booking.draft([booking])
            self.assertEqual(booking.state, 'draft')

    @with_transaction()
    def test_restaurant_table(self):
        "Test seats and overlapping restaurant bookings"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Table = pool.get('hotel.restaurant.table')
            TableBooking = pool.get('hotel.restaurant.booking')
            hotel, _, _, party, _, _ = self._services(company)
            table, = Table.create([{
                        'hotel': hotel.id, 'name': "T1", 'seats': 4}])
            data = {
                'hotel': hotel.id, 'table': table.id, 'date': TODAY,
                'time': datetime.time(19, 0), 'duration': 90,
                'covers': 4, 'guest_name': "Smith"}
            first, = TableBooking.create([data])
            second, = TableBooking.create([dict(
                        data, time=datetime.time(22, 0))])
            TableBooking.seat([first])
            TableBooking.finish([first])
            self.assertEqual(first.state, 'done')
            TableBooking.no_show([second])
            self.assertEqual(second.state, 'no_show')
            # A no-show frees the table
            TableBooking.create([dict(data, time=datetime.time(22, 30))])
            # Failed creations stay in the transaction: keep them last
            with self.assertRaises(ServiceError):
                TableBooking.create([dict(
                            data, covers=5, time=datetime.time(12, 0))])
            TableBooking.create([dict(data, time=datetime.time(17, 0))])
            with self.assertRaises(ServiceError):
                TableBooking.create([dict(data, time=datetime.time(18, 0))])

    @with_transaction()
    def test_unique_names(self):
        "Test resource names are unique per hotel"
        company = create_company()
        with set_company(company):
            from trytond.exceptions import UserError
            Resource = Pool().get('hotel.service.resource')
            hotel, _, _, _, _, anna = self._services(company)
            with self.assertRaises(UserError):
                Resource.create([{'hotel': hotel.id, 'name': "Anna"}])
