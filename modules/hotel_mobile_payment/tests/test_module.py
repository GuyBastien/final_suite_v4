# This file is part of the Tryton hotel_mobile_payment module.
import datetime
import unittest
from decimal import Decimal

from trytond.modules.company.tests import CompanyTestMixin, set_company
from trytond.modules.hotel_billing.exceptions import BillingError
from trytond.modules.hotel_pms.tests.helpers import (
    make_reservation, setup_hotel)
from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tests.fixture import MobileFixture
from trytond.modules.mobile_payment.tests.test_module import fake_provider
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction

START = datetime.datetime.combine(
    datetime.date.today(), datetime.time(0, 0))
END = START + datetime.timedelta(days=2)


class HotelMobilePaymentTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test hotel_mobile_payment module"
    module = 'hotel_mobile_payment'

    def _scenario(self, results, scenario, confirm=True):
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        company = self._company()
        with set_company(company):
            hotel, room_type, (room1, room2), party = setup_hotel(company)
            point, product, clearing = self._pos(company)
            patches = fake_provider(results)
            for p in patches:
                p.start()
            try:
                account = self._account(company, clearing, 'fake')
                stay = make_reservation(
                    company, hotel, party, room_type, room1,
                    start=START, end=END)
                if confirm:
                    Reservation.confirm([stay])
                scenario(Reservation(stay.id), account)
            finally:
                for p in patches:
                    p.stop()

    @with_transaction()
    def test_stay_paid_by_mobile(self):
        "A confirmed payment becomes a deposit that lowers the balance"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Reservation = pool.get('hotel.reservation')

        def scenario(stay, account):
            due = stay.balance_due
            self.assertGreater(due, 0)
            part = due / 2
            tx, = Tx.create([{
                        'reservation': stay.id, 'account': account.id,
                        'amount': part}])
            self.assertEqual(tx.document, stay.rec_name)
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            stay = Reservation(stay.id)
            deposit, = stay.deposits
            self.assertEqual(deposit.method, 'mobile')
            self.assertEqual(deposit.state, 'received')
            self.assertEqual(deposit.amount, part)
            self.assertEqual(deposit.mobile_transaction, tx)
            self.assertEqual(stay.balance_due, due - part)
            # cannot ask more than what is still due
            tx2, = Tx.create([{
                        'reservation': stay.id, 'account': account.id,
                        'amount': due}])
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx2])

        self._scenario({
                'create': {'checkout_url': 'https://pay.example/h1',
                    'provider_ref': 'H1'},
                'check': [{'status': 'succeeded', 'provider_ref': 'H1'}],
                }, scenario)

    @with_transaction()
    def test_wrong_amount_needs_review(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Reservation = pool.get('hotel.reservation')

        def scenario(stay, account):
            tx, = Tx.create([{
                        'reservation': stay.id, 'account': account.id,
                        'amount': Decimal('10')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            self.assertTrue(Tx(tx.id).orphan)
            self.assertFalse(Reservation(stay.id).deposits)

        self._scenario({
                'create': {'checkout_url': 'https://pay.example/h2'},
                'check': [{'status': 'succeeded', 'amount': Decimal('9')}],
                }, scenario)

    @with_transaction()
    def test_draft_stay_not_payable(self):
        "Only confirmed or checked-in stays can be paid"
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(stay, account):
            self.assertEqual(stay.state, 'draft')
            tx, = Tx.create([{
                        'reservation': stay.id, 'account': account.id,
                        'amount': Decimal('10')}])
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])

        self._scenario({'create': {}, 'check': []}, scenario, confirm=False)

    @with_transaction()
    def test_mobile_deposit_cannot_be_typed(self):
        Deposit = Pool().get('hotel.deposit')

        def scenario(stay, account):
            with self.assertRaises(BillingError):
                Deposit.create([{
                            'reservation': stay.id, 'method': 'mobile',
                            'amount': Decimal('10')}])
            Deposit.create([{
                        'reservation': stay.id, 'method': 'cash',
                        'amount': Decimal('10')}])

        self._scenario({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_wizard_defaults(self):
        Wizard = Pool().get('mobile.payment.pay', type='wizard')

        def scenario(stay, account):
            defaults = Wizard._target_start('hotel.reservation', stay.id)
            self.assertEqual(defaults['amount'], stay.balance_due)
            self.assertEqual(
                Wizard._target_values('hotel.reservation', stay.id),
                {'reservation': stay.id})

        self._scenario({'create': {}, 'check': []}, scenario)


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            HotelMobilePaymentTestCase))
    return suite
