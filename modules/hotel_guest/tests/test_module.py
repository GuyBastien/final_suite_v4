# This file is part of the Tryton hotel_guest module.
import datetime
from decimal import Decimal
from unittest.mock import patch

from trytond.model.exceptions import ValidationError
from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import GuestError

START = datetime.datetime(2030, 1, 7, 14, 0)
END = datetime.datetime(2030, 1, 9, 11, 0)      # 2 nights


class GuestTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_guest module"
    module = 'hotel_guest'

    def _setup(self, company):
        pool = Pool()
        Room = pool.get('hotel.room')
        Party = pool.get('party.party')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room, = Room.create([{
                    'hotel': hotel.id, 'name': '101', 'type': room_type.id}])
        party, = Party.create([{'name': "Guest", 'first_name': "Ann",
                    'is_person': True}])
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

    def _stay(self, reservation):
        Reservation = Pool().get('hotel.reservation')
        Reservation.confirm([reservation])
        Reservation.check_in([reservation])
        Reservation.check_out([reservation])

    @with_transaction()
    def test_loyalty_earned_once_and_tier(self):
        "Test points are earned once at check-out and set the tier"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Program = pool.get('hotel.loyalty.program')
            Entry = pool.get('hotel.loyalty.entry')
            Party = pool.get('party.party')
            hotel, room_type, room, party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            # no program: nothing earned
            self._stay(reservation)
            self.assertEqual(Entry.search([]), [])
            Program.create([{
                        'company': company.id,
                        'points_per_currency': Decimal('1.5'),
                        'silver_threshold': 100, 'gold_threshold': 250}])
            entry = Entry.earn(reservation)
            # 2 nights x 100 x 1.5
            self.assertEqual(entry.points, 300)
            self.assertIsNone(Entry.earn(reservation))
            party = Party(party.id)
            self.assertEqual(party.loyalty_points, 300)
            self.assertEqual(party.loyalty_tier, 'gold')
            self.assertEqual(party.stays_count, 1)
            Entry.create([{'party': party.id, 'kind': 'redeem',
                        'points': -150, 'reason': "Free night"}])
            party = Party(party.id)
            self.assertEqual(party.loyalty_points, 150)
            self.assertEqual(party.loyalty_tier, 'silver')
            with self.assertRaises(Exception):
                Program.create([{'company': company.id}])

    @with_transaction()
    def test_program_thresholds(self):
        "Test the gold threshold cannot be under the silver one"
        company = create_company()
        with set_company(company):
            Program = Pool().get('hotel.loyalty.program')
            with self.assertRaises(ValidationError):
                Program.create([{
                            'company': company.id,
                            'points_per_currency': Decimal(1),
                            'silver_threshold': 500,
                            'gold_threshold': 100}])

    @with_transaction()
    def test_blacklisted_guest(self):
        "Test a blacklisted guest cannot be confirmed"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, room, party = self._setup(company)
            party.blacklisted = True
            party.blacklist_reason = "Damages"
            party.save()
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            with self.assertRaises(GuestError):
                Reservation.confirm([reservation])
            self.assertEqual(reservation.state, 'draft')
            party.blacklisted = False
            party.save()
            Reservation.confirm([reservation])
            self.assertEqual(reservation.state, 'confirmed')

    @with_transaction()
    def test_registration_required(self):
        "Test check-in needs one registration per guest when required"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Registration = pool.get('hotel.guest.registration')
            hotel, room_type, room, party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room, children=1)
            Reservation.confirm([reservation])
            # not required by default
            self.assertFalse(hotel.require_registration)
            hotel.require_registration = True
            hotel.save()
            reservation = Reservation(reservation.id)
            Registration.create([{
                        'reservation': reservation.id, 'name': "Guest",
                        'id_type': 'passport', 'id_number': 'X1'}])
            with self.assertRaises(GuestError):
                Reservation.check_in([Reservation(reservation.id)])
            Registration.create([{
                        'reservation': reservation.id, 'name': "Kid"}])
            Reservation.check_in([Reservation(reservation.id)])
            self.assertEqual(
                Reservation(reservation.id).state, 'checked_in')
            self.assertEqual(
                Registration(Registration.search([])[0].id).company, company)

    @with_transaction()
    def test_anonymize(self):
        "Test GDPR anonymization"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Party = pool.get('party.party')
            Registration = pool.get('hotel.guest.registration')
            hotel, room_type, room, party = self._setup(company)
            party.id_number = 'P123'
            party.preferences = "Quiet room"
            party.vip = True
            party.gdpr_consent = True
            party.contact_mechanisms = [{'type': 'email',
                    'value': 'ann@example.com'}]
            party.save()
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            Registration.create([{
                        'reservation': reservation.id, 'name': "Companion",
                        'id_number': 'C1'}])
            with self.assertRaises(GuestError):
                Party.anonymize([party])
            self._stay(reservation)
            Party.anonymize([Party(party.id)])
            with Transaction().set_context(active_test=False):
                party = Party(party.id)
            self.assertTrue(party.anonymized)
            self.assertFalse(party.active)
            self.assertIsNone(party.id_number)
            self.assertIsNone(party.preferences)
            self.assertFalse(party.vip)
            self.assertFalse(party.gdpr_consent)
            self.assertEqual(party.contact_mechanisms, ())
            self.assertEqual(party.name, "Anonymized")
            registration, = Registration.search([])
            self.assertEqual(registration.name, "Anonymized")
            self.assertIsNone(registration.id_number)

    @with_transaction()
    def test_keycards(self):
        "Test key cards: workflow, hooks and revocation at check-out"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Keycard = pool.get('hotel.keycard')
            hotel, room_type, room, party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            card1, card2 = Keycard.create([
                    {'reservation': reservation.id, 'room': room.id,
                        'code': 'A1'},
                    {'reservation': reservation.id, 'room': room.id,
                        'code': 'A2'}])
            self.assertEqual(card1.state, 'draft')
            with patch.object(Keycard, '_provider_issue') as issue, \
                    patch.object(Keycard, '_provider_revoke') as revoke:
                Keycard.issue([card1, card2])
                self.assertEqual(issue.call_count, 2)
                Keycard.give_back([card2])
                self.assertEqual(revoke.call_count, 1)
                self.assertEqual(card2.state, 'returned')
                Reservation.confirm([reservation])
                Reservation.check_in([reservation])
                Reservation.check_out([reservation])
                self.assertEqual(revoke.call_count, 2)
            self.assertEqual(Keycard(card1.id).state, 'revoked')
            self.assertEqual(Keycard(card1.id).company, company)
            # A revoked card cannot be issued again
            Keycard.issue([Keycard(card1.id)])
            self.assertEqual(Keycard(card1.id).state, 'revoked')

    @with_transaction()
    def test_keycard_on_change(self):
        "Test the card takes the dates and room of the reservation"
        company = create_company()
        with set_company(company):
            Keycard = Pool().get('hotel.keycard')
            hotel, room_type, room, party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room)
            card = Keycard(reservation=reservation)
            card.on_change_reservation()
            self.assertEqual(card.valid_from, START)
            self.assertEqual(card.valid_to, END)
            self.assertEqual(card.room, room)

