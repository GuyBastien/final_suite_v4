# This file is part of the Tryton hotel_communication module.
import datetime
from unittest.mock import patch

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from .. import message as message_module
from ..exceptions import CommunicationError
from ..message import render_text

START = datetime.datetime(2030, 1, 7, 14, 0)
END = datetime.datetime(2030, 1, 9, 11, 0)


class CommunicationTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_communication module"
    module = 'hotel_communication'

    def _setup(self, company, email='ann@example.com', consent=False):
        pool = Pool()
        Room = pool.get('hotel.room')
        Party = pool.get('party.party')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room, = Room.create([{
                    'hotel': hotel.id, 'name': '101', 'type': room_type.id}])
        values = {
            'name': "Guest", 'first_name': "Ann", 'is_person': True,
            'marketing_consent': consent}
        if email:
            values['contact_mechanisms'] = [
                ('create', [{'type': 'email', 'value': email}])]
        party, = Party.create([values])
        return hotel, room_type, room, party

    def _reservation(self, company, hotel, party, room_type, room, **values):
        Reservation = Pool().get('hotel.reservation')
        data = {
            'company': company.id, 'hotel': hotel.id, 'party': party.id,
            'start': START, 'end': END,
            'lines': [('create', [{
                            'room_type': room_type.id, 'room': room.id}])],
            }
        data.update(values)
        return Reservation.create([data])[0]

    def test_render_text(self):
        "Test only plain placeholders are substituted"
        values = {'guest_name': "Ann", 'hotel': "Grand"}
        self.assertEqual(
            render_text("Hi {guest_name} at {hotel} {unknown}", values),
            "Hi Ann at Grand {unknown}")
        # Attribute access is not possible
        self.assertEqual(
            render_text("{guest_name.__class__}", values),
            "{guest_name.__class__}")
        self.assertEqual(render_text(None, values), '')

    @with_transaction()
    def test_default_templates_and_confirmation(self):
        "Test the default templates and the message queued at confirmation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Template = pool.get('hotel.message.template')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            self.assertEqual(len(Template.search([])), 4)
            # Running it again creates nothing
            Hotel.create_default_templates([hotel])
            self.assertEqual(len(Template.search([])), 4)
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            self.assertEqual(Message.search([]), [])
            Reservation.confirm([reservation])
            message, = Message.search([])
            self.assertEqual(message.event, 'confirmation')
            self.assertEqual(message.state, 'queued')
            self.assertEqual(message.recipient, 'ann@example.com')
            self.assertIn(reservation.number, message.subject)
            self.assertIn("Ann Guest", message.body)
            self.assertIn("Grand Hotel", message.body)
            self.assertNotIn('{', message.body)
            with self.assertRaises(CommunicationError):
                Message.delete([message])

    @with_transaction()
    def test_send_success_and_failure(self):
        "Test sending records the result and failed messages can be retried"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            Reservation.confirm([reservation])
            message, = Message.search([])
            with patch.object(message_module, 'sendmail') as sendmail, \
                    patch.object(message_module.config, 'get',
                        side_effect=lambda s, o, *a, **k:
                        'hotel@example.com' if (s, o) == ('email', 'from')
                        else None):
                Message.send([message])
                self.assertEqual(sendmail.call_count, 1)
            self.assertEqual(message.state, 'sent')
            self.assertEqual(message.attempts, 1)
            self.assertIsNotNone(message.sent_at)
            # A sent message is not sent twice
            with patch.object(message_module, 'sendmail') as sendmail:
                Message.send([message])
                sendmail.assert_not_called()
            # A failure is recorded, not raised
            Reservation.cancel([reservation])
            cancelled = Message.search([('event', '=', 'cancellation')])[0]
            with patch.object(
                    message_module, 'sendmail', side_effect=OSError("down")), \
                    patch.object(message_module.config, 'get',
                        side_effect=lambda s, o, *a, **k:
                        'hotel@example.com' if (s, o) == ('email', 'from')
                        else None):
                Message.send([cancelled])
            self.assertEqual(cancelled.state, 'failed')
            self.assertEqual(cancelled.error, "down")
            Message.retry([cancelled])
            self.assertEqual(cancelled.state, 'queued')
            Message.cancel([cancelled])
            self.assertEqual(cancelled.state, 'cancelled')
            Message.delete([cancelled])

    @with_transaction()
    def test_no_sender_is_a_recorded_failure(self):
        "Test a missing sender address makes the message fail"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            Reservation.confirm([reservation])
            message, = Message.search([])
            with patch.object(message_module.config, 'get',
                    return_value=None):
                Message.send([message])
            self.assertEqual(message.state, 'failed')
            self.assertTrue(message.error)

    @with_transaction()
    def test_marketing_consent_and_contact(self):
        "Test marketing messages need consent and guests need a contact"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            # No consent: no thank-you message
            self.assertEqual(
                Message.queue(reservation, 'thank_you'), [])
            self.assertEqual(
                len(Message.queue(reservation, 'thank_you', force=True)), 1)
            # Without e-mail nothing can be sent
            nobody, = pool.get('party.party').create([{'name': "Nobody"}])
            Reservation.confirm([reservation])
            quiet = self._reservation(
                company, hotel, nobody, room_type, room,
                start=START + datetime.timedelta(days=10),
                end=END + datetime.timedelta(days=10))
            self.assertEqual(Message.queue(quiet, 'confirmation'), [])

    @with_transaction()
    def test_anonymized_party_gets_nothing(self):
        "Test an anonymized guest is never contacted"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Party = pool.get('party.party')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            Party.write([party], {'anonymized': True})
            self.assertEqual(Message.queue(reservation, 'confirmation'), [])

    @with_transaction()
    def test_reminders_and_precheckin(self):
        "Test the pre-arrival reminder, its link and the pre-check-in"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            Hotel.create_default_templates([hotel])
            arrival = datetime.datetime.combine(
                datetime.date.today() + datetime.timedelta(days=2),
                datetime.time(15, 0))
            reservation = self._reservation(
                company, hotel, party, room_type, room,
                start=arrival, end=arrival + datetime.timedelta(days=2))
            Reservation.confirm([reservation])
            Message.generate_reminders()
            reminder, = Message.search([('event', '=', 'pre_arrival')])
            self.assertIn('/hotel/precheckin/', reminder.body)
            token = Reservation(reservation.id).precheckin_token
            self.assertTrue(token)
            self.assertIn(token, reminder.body)
            # Running again does not duplicate the reminder
            Message.generate_reminders()
            self.assertEqual(
                len(Message.search([('event', '=', 'pre_arrival')])), 1)
            # The guest gives the arrival time
            self.assertIsNone(Reservation.apply_precheckin(
                    'unknown', datetime.time(18, 0)))
            found = Reservation.apply_precheckin(token, datetime.time(18, 30))
            self.assertEqual(found.precheckin_eta, datetime.time(18, 30))
            self.assertIsNotNone(found.precheckin_at)
            # The link stops working once the guest has arrived
            Reservation.check_in([Reservation(reservation.id)])
            self.assertIsNone(Reservation.by_precheckin_token(token))

    @with_transaction()
    def test_send_precheckin_button(self):
        "Test the button queues the link even outside the reminder window"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Message = pool.get('hotel.message')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            # No template yet
            with self.assertRaises(CommunicationError):
                Reservation.send_precheckin([reservation])
            Hotel.create_default_templates([hotel])
            Reservation.send_precheckin([reservation])
            message, = Message.search([('event', '=', 'pre_arrival')])
            self.assertIn('/hotel/precheckin/', message.body)
