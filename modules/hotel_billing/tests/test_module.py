# This file is part of the Tryton hotel_billing module.
import datetime
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel_pms.tests.helpers import (
    make_reservation, service, setup_hotel)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from ..exceptions import BillingError

START = datetime.datetime.combine(
    datetime.date.today(), datetime.time(0, 0))
END = START + datetime.timedelta(days=2)


class BillingTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_billing module"
    module = 'hotel_billing'

    def _stay(self, company, **values):
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        hotel, room_type, (room1, room2), party = setup_hotel(company)
        reservation = make_reservation(
            company, hotel, party, room_type, room1, start=START, end=END,
            **values)
        Reservation.confirm([reservation])
        return hotel, room_type, (room1, room2), party, reservation

    def _out(self, reservation):
        Reservation = Pool().get('hotel.reservation')
        Reservation.check_in([reservation])
        Reservation.check_out([reservation])
        return Reservation(reservation.id)

    @with_transaction()
    def test_tourist_tax(self):
        "Test the tourist tax charged once at check-out"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Tax = pool.get('hotel.tourist.tax')
            hotel, _, _, _, reservation = self._stay(
                company, adults=2, children=1)
            Tax.create([{
                        'hotel': hotel.id, 'name': "City tax",
                        'product': service("Tax", '0').id,
                        'amount': Decimal('1.5')}])
            reservation = self._out(reservation)
            charge, = reservation.charges
            self.assertTrue(charge.tourist_tax)
            # 2 adults x 2 nights, the child is exempt
            self.assertEqual(charge.quantity, 4.)
            self.assertEqual(charge.amount, Decimal('6.00'))
            reservation.import_consumptions()
            self.assertEqual(len(pool.get('hotel.reservation')(
                        reservation.id).charges), 1)

    @with_transaction()
    def test_tourist_tax_options(self):
        "Test children, maximum nights and exemption"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Tax = pool.get('hotel.tourist.tax')
            hotel, _, _, _, reservation = self._stay(
                company, adults=1, children=2)
            Tax.create([{
                        'hotel': hotel.id, 'name': "Tax",
                        'product': service("Tax", '0').id,
                        'amount': Decimal('2'), 'children_exempt': False,
                        'max_nights': 1}])
            reservation = self._out(reservation)
            charge, = reservation.charges
            self.assertEqual(charge.quantity, 3.)
            other = make_reservation(
                company, hotel, reservation.party,
                reservation.lines[0].room_type, reservation.lines[0].room.type.rooms[1],
                start=START, end=END, tourist_tax_exempt=True)
            pool.get('hotel.reservation').confirm([other])
            self.assertFalse(self._out(other).charges)

    @with_transaction()
    def test_deposit_applied_to_sale(self):
        "Test deposits are deducted from the sale then applied"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Deposit = pool.get('hotel.deposit')
            Reservation = pool.get('hotel.reservation')
            hotel, _, _, _, reservation = self._stay(company)
            first, second = Deposit.create([
                    {'reservation': reservation.id, 'amount': Decimal('50')},
                    {'reservation': reservation.id, 'amount': Decimal('30')},
                    ])
            reservation = Reservation(reservation.id)
            self.assertEqual(reservation.deposit_total, Decimal('80.00'))
            self.assertEqual(reservation.balance_due, Decimal('120.00'))
            Deposit.refund([second])
            self.assertEqual(
                Reservation(reservation.id).deposit_total, Decimal('50.00'))
            with self.assertRaises(BillingError):
                Reservation.create_sale([reservation])
            hotel.deposit_product = service("Deposit", '0')
            hotel.save()
            Reservation.create_sale([Reservation(reservation.id)])
            sale = Reservation(reservation.id).sale
            self.assertEqual(len(sale.lines), 2)
            self.assertEqual(sale.untaxed_amount, Decimal('150.00'))
            self.assertEqual(Deposit(first.id).state, 'applied')
            self.assertEqual(Deposit(second.id).state, 'refunded')
            with self.assertRaises(BillingError):
                Deposit.delete([Deposit(first.id)])
            with self.assertRaises(Exception):
                Deposit.create([{
                            'reservation': reservation.id,
                            'amount': Decimal('0')}])

    @with_transaction()
    def test_split_sales(self):
        "Test one sale per payer and the credit limit"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Party = pool.get('party.party')
            Reservation = pool.get('hotel.reservation')
            Charge = pool.get('hotel.reservation.charge')
            hotel, room_type, _, guest, reservation = self._stay(company)
            corp, = Party.create([{
                        'name': "ACME", 'hotel_corporate': True,
                        'hotel_credit_limit': Decimal('150')}])
            line, = reservation.lines
            line.payer = corp
            line.save()
            product = service("Breakfast", '10')
            Charge.create([{
                        'reservation': reservation.id, 'product': product.id,
                        'quantity': 2., 'unit': product.sale_uom.id,
                        'unit_price': Decimal('10')}])
            reservation = Reservation(reservation.id)
            # The room (200) exceeds the credit limit of the company
            with self.assertRaises(BillingError):
                reservation.process_split_sales()
            corp.hotel_credit_limit = Decimal('500')
            corp.save()
            sales = Reservation(reservation.id).process_split_sales()
            by_party = {s.party.id: s for s in sales}
            self.assertEqual(set(by_party), {guest.id, corp.id})
            self.assertEqual(by_party[corp.id].untaxed_amount,
                Decimal('200.00'))
            self.assertEqual(by_party[guest.id].untaxed_amount,
                Decimal('20.00'))
            reservation = Reservation(reservation.id)
            self.assertEqual(len(reservation.sales), 2)
            with self.assertRaises(BillingError):
                reservation.process_split_sales()

    @with_transaction()
    def test_night_audit(self):
        "Test the figures of the night audit and the no-show handling"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Audit = pool.get('hotel.night.audit')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party, reservation = \
                self._stay(company)
            Reservation.check_in([reservation])
            no_show = make_reservation(
                company, hotel, party, room_type, room2, start=START,
                end=END)
            Reservation.confirm([no_show])
            audit, = Audit.create([{
                        'hotel': hotel.id, 'date': START.date()}])
            Audit.close_day([audit])
            audit = Audit(audit.id)
            self.assertEqual(audit.state, 'done')
            self.assertEqual(audit.in_house, 1)
            self.assertEqual(audit.arrivals, 1)
            self.assertEqual(audit.no_shows, 1)
            self.assertEqual(audit.room_revenue, Decimal('100.00'))
            self.assertTrue(audit.user)
            no_show = Reservation(no_show.id)
            self.assertTrue(no_show.no_show)
            self.assertEqual(no_show.state, 'cancelled')
            self.assertIn(no_show.number, audit.notes)
            with self.assertRaises(BillingError):
                Audit.delete([audit])
            with self.assertRaises(Exception):
                Audit.create([{'hotel': hotel.id, 'date': START.date()}])

