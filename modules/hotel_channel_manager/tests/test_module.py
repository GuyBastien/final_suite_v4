# This file is part of the Tryton hotel_channel_manager module.
import datetime
from unittest.mock import patch

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from .. import ical
from ..exceptions import ChannelError

TODAY = datetime.date.today()


def day(n):
    return TODAY + datetime.timedelta(days=n)


def feed(*events):
    "Calendar text; events are (uid, start, end[, status])"
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0']
    for event in events:
        uid, start, end = event[:3]
        lines += [
            'BEGIN:VEVENT', 'UID:' + uid,
            'DTSTART;VALUE=DATE:' + start.strftime('%Y%m%d'),
            'DTEND;VALUE=DATE:' + end.strftime('%Y%m%d'),
            'SUMMARY:Guest\\, Test']
        if len(event) > 3:
            lines.append('STATUS:' + event[3])
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines) + '\r\n'


class ChannelTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_channel_manager module"
    module = 'hotel_channel_manager'

    def _setup(self, company, url='https://ota.example/room1.ics'):
        pool = Pool()
        Room = pool.get('hotel.room')
        Party = pool.get('party.party')
        Channel = pool.get('hotel.channel')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room, = Room.create([
                {'hotel': hotel.id, 'name': '101', 'type': room_type.id}])
        party, = Party.create([{'name': "OTA"}])
        channel, = Channel.create([{
                    'name': "Airbnb", 'hotel': hotel.id, 'provider': 'airbnb',
                    'party': party.id,
                    'mappings': [('create', [{
                                    'room': room.id, 'import_url': url}])],
                    }])
        return hotel, room_type, room, party, channel

    def _sync(self, channel, text):
        Mapping = Pool().get('hotel.channel.mapping')
        with patch.object(Mapping, '_fetch', return_value=text):
            channel.synchronize([channel])

    def _own(self, company, hotel, party, room_type, room, start, end):
        Reservation = Pool().get('hotel.reservation')
        reservation, = Reservation.create([{
                    'company': company.id, 'hotel': hotel.id,
                    'party': party.id,
                    'start': datetime.datetime.combine(start, datetime.time(14)),
                    'end': datetime.datetime.combine(end, datetime.time(11)),
                    'lines': [('create', [{
                                    'room_type': room_type.id,
                                    'room': room.id}])]}])
        Reservation.confirm([reservation])
        return reservation

    def test_ical_roundtrip(self):
        "Test the calendar reader and writer"
        text = ical.build([{
                    'uid': 'a@x', 'summary': 'Long, name; ' + 'x' * 120,
                    'start': day(1), 'end': day(3)}])
        self.assertTrue(text.endswith('\r\n'))
        self.assertTrue(all(len(l.encode()) <= 75
                for l in text.split('\r\n')))
        events = ical.parse(text)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['uid'], 'a@x')
        self.assertEqual(events[0]['start'], day(1))
        self.assertEqual(events[0]['end'], day(3))
        self.assertTrue(events[0]['summary'].startswith('Long, name; xxx'))

    def test_ical_parse_variants(self):
        "Test date-times, missing end and cancelled events"
        events = ical.parse(
            'BEGIN:VEVENT\nUID:1\nDTSTART:20300101T140000Z\n'
            'DTEND:20300103T100000Z\nSTATUS:CANCELLED\nEND:VEVENT\n'
            'BEGIN:VEVENT\nUID:2\nDTSTART;VALUE=DATE:20300105\nEND:VEVENT\n'
            'BEGIN:VEVENT\nDTSTART:20300105\nEND:VEVENT\n')
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0]['start'], datetime.datetime(2030, 1, 1, 14))
        self.assertEqual(events[0]['status'], 'CANCELLED')
        self.assertEqual(events[1]['end'], datetime.date(2030, 1, 6))

    @with_transaction()
    def test_import_creates_reservation(self):
        "Test an imported event blocks the room, once"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Booking = pool.get('hotel.channel.booking')
            Log = pool.get('hotel.channel.log')
            hotel, room_type, room, party, channel = self._setup(company)
            text = feed(('ev1', day(10), day(12)))
            self._sync(channel, text)
            booking, = Booking.search([])
            self.assertEqual(booking.state, 'confirmed')
            reservation = booking.reservation
            self.assertEqual(reservation.state, 'confirmed')
            self.assertEqual(reservation.channel, channel)
            self.assertEqual(reservation.external_reference, 'ev1')
            self.assertEqual(reservation.party, party)
            self.assertEqual(reservation.start.date(), day(10))
            self.assertFalse(room.is_available(
                    reservation.start, reservation.end))
            self._sync(channel, text)
            self.assertEqual(Booking.search_count([]), 1)
            log = Log.search([], limit=1)[0]
            self.assertEqual((log.state, log.created), ('success', 0))
            self.assertEqual(channel.last_synchronization is not None, True)

    @with_transaction()
    def test_import_change_and_cancel(self):
        "Test moved and removed events"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Booking = pool.get('hotel.channel.booking')
            hotel, room_type, room, party, channel = self._setup(company)
            self._sync(channel, feed(('ev1', day(10), day(12))))
            first, = Booking.search([])
            old = first.reservation
            self._sync(channel, feed(('ev1', day(20), day(22))))
            booking, = Booking.search([])
            self.assertEqual(old.state, 'cancelled')
            self.assertEqual(booking.reservation.start.date(), day(20))
            self.assertEqual(booking.reservation.state, 'confirmed')
            self._sync(channel, feed(('ev1', day(20), day(22), 'CANCELLED')))
            booking, = Booking.search([])
            self.assertEqual(booking.state, 'cancelled')
            self.assertEqual(booking.reservation, None)
            # reappears
            self._sync(channel, feed(('ev1', day(20), day(22))))
            booking, = Booking.search([])
            self.assertEqual(booking.state, 'confirmed')
            # vanishes from the feed
            self._sync(channel, feed())
            booking, = Booking.search([])
            self.assertEqual(booking.state, 'cancelled')

    @with_transaction()
    def test_conflict_and_notification(self):
        "Test an overbooking is flagged, notified and can be retried"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Booking = pool.get('hotel.channel.booking')
            Notification = pool.get('hotel.notification')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party, channel = self._setup(company)
            own = self._own(
                company, hotel, party, room_type, room, day(10), day(12))
            self._sync(channel, feed(('ev1', day(11), day(13))))
            booking, = Booking.search([])
            self.assertEqual(booking.state, 'conflict')
            self.assertEqual(booking.reservation, None)
            self.assertTrue(booking.message)
            Notification.generate([hotel])
            alerts = Notification.search([
                    ('hotel', '=', hotel.id), ('kind', '=', 'alert'),
                    ('key', 'like', 'chconflict:%')])
            self.assertEqual(len(alerts), 1)
            Reservation.cancel([own])
            Booking.retry([booking])
            self.assertEqual(Booking(booking.id).state, 'confirmed')
            Notification.generate([hotel])
            self.assertEqual(Notification.search_count([
                        ('key', 'like', 'chconflict:%'),
                        ('state', '=', 'open')]), 0)

    @with_transaction()
    def test_export(self):
        "Test the exported calendar and its token"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Mapping = pool.get('hotel.channel.mapping')
            hotel, room_type, room, party, channel = self._setup(company)
            mapping, = channel.mappings
            self.assertTrue(mapping.token)
            self.assertIn('/hotel/ical/%s.ics' % mapping.token,
                mapping.export_path)
            self._own(
                company, hotel, party, room_type, room, day(5), day(7))
            self._sync(channel, feed(('ev1', day(10), day(12))))
            # the channel does not get its own booking back
            events = ical.parse(Mapping.ical_by_token(mapping.token))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['start'], day(5))
            self.assertEqual(events[0]['end'], day(7))
            self.assertEqual(events[0]['summary'], 'Reserved')
            self.assertIsNone(Mapping.ical_by_token('unknown'))
            old = mapping.token
            Mapping.new_token([mapping])
            self.assertNotEqual(Mapping(mapping.id).token, old)
            self.assertIsNone(Mapping.ical_by_token(old))

    @with_transaction()
    def test_other_channel_is_exported(self):
        "Test the bookings of a channel block the rooms of another one"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Mapping = pool.get('hotel.channel.mapping')
            Channel = pool.get('hotel.channel')
            hotel, room_type, room, party, channel = self._setup(company)
            other, = Channel.create([{
                        'name': "Expedia", 'hotel': hotel.id,
                        'provider': 'expedia', 'party': party.id,
                        'mappings': [('create', [{'room': room.id}])]}])
            self._sync(channel, feed(('ev1', day(10), day(12))))
            events = ical.parse(
                Mapping.ical_by_token(other.mappings[0].token))
            self.assertEqual([e['start'] for e in events], [day(10)])

    @with_transaction()
    def test_fetch_errors(self):
        "Test errors are logged and notified, bad URLs refused"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Mapping = pool.get('hotel.channel.mapping')
            Log = pool.get('hotel.channel.log')
            Notification = pool.get('hotel.notification')
            hotel, room_type, room, party, channel = self._setup(company)
            with patch.object(Mapping, '_fetch',
                    side_effect=ChannelError("boom")):
                channel.synchronize([channel])
            log, = Log.search([])
            self.assertEqual(log.state, 'error')
            self.assertEqual(log.message, "boom")
            Notification.generate([hotel])
            self.assertEqual(Notification.search_count([
                        ('key', 'like', 'cherror:%'),
                        ('kind', '=', 'warning')]), 1)
            mapping, = channel.mappings
            with self.assertRaises(ChannelError):
                Mapping.write([mapping], {'import_url': 'file:///etc/passwd'})
            with self.assertRaises(ChannelError):
                Mapping._fetch('ftp://example.com/x.ics')

    def test_route_and_cron(self):
        "Test the public route is registered"
        from trytond.wsgi import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        self.assertIn(
            '/<string:database_name>/hotel/ical/<string:token>.ics', rules)


del ModuleTestCase
