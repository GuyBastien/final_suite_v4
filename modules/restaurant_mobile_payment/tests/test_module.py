# This file is part of the Tryton restaurant_mobile_payment module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import CompanyTestMixin, set_company
from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tests.fixture import MobileFixture
from trytond.modules.mobile_payment.tests.test_module import fake_provider
from trytond.modules.restaurant.exceptions import OrderError
from trytond.modules.restaurant.tests.test_module import (
    create_item, setup_restaurant)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class RestaurantMobilePaymentTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test restaurant_mobile_payment module"
    module = 'restaurant_mobile_payment'

    def _scenario(self, results, scenario):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            patches = fake_provider(results)
            for p in patches:
                p.start()
            try:
                account = self._account(company, clearing, 'fake')
                restaurant, t1, t2, _ = setup_restaurant(company)
                scenario(restaurant, t1, account)
            finally:
                for p in patches:
                    p.stop()

    def _order(self, restaurant, table, price='100'):
        pool = Pool()
        Order = pool.get('restaurant.order')
        Line = pool.get('restaurant.order.line')
        item = create_item("Griot", price, type='service')
        order, = Order.create([{
                    'restaurant': restaurant.id,
                    'company': restaurant.company.id,
                    'type': 'dine_in', 'table': table.id}])
        Line.create([{
                    'order': order.id, 'product': item.id, 'quantity': 1.0,
                    'unit_price': item.list_price}])
        return Order(order.id)

    @with_transaction()
    def test_order_paid_by_mobile(self):
        "A confirmed mobile payment is booked on the order, then it closes"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Order = pool.get('restaurant.order')

        def scenario(restaurant, table, account):
            order = self._order(restaurant, table)
            self.assertEqual(order.balance, Decimal('110'))  # 10% service
            tx, = Tx.create([{
                        'restaurant_order': order.id, 'account': account.id,
                        'amount': order.balance}])
            self.assertTrue(tx.order_ref.startswith(order.number))
            self.assertEqual(tx.document, order.rec_name)
            Tx.request([tx])
            # closing or cancelling is refused while waiting
            with self.assertRaises(MobilePaymentError):
                Order.pay([Order(order.id)])
            with self.assertRaises(MobilePaymentError):
                Order.cancel([Order(order.id)])
            Tx.check([Tx(tx.id)])  # still waiting
            self.assertEqual(Tx(tx.id).state, 'pending')
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            order = Order(order.id)
            payment, = order.payments
            self.assertEqual(payment.method, 'mobile')
            self.assertEqual(payment.amount, Decimal('110'))
            self.assertEqual(payment.mobile_transaction, tx)
            self.assertEqual(order.balance, Decimal('0'))
            self.assertEqual(order.state, 'open')  # closed by the waiter
            Order.pay([order])
            self.assertEqual(Order(order.id).state, 'paid')
            # verifying again books nothing more
            Tx.check([tx])
            self.assertEqual(len(Order(order.id).payments), 1)

        self._scenario({
                'create': {'checkout_url': 'https://pay.example/r1',
                    'provider_ref': 'T9'},
                'check': [
                    {'status': 'pending'},
                    {'status': 'succeeded', 'amount': Decimal('110'),
                        'provider_ref': 'T9'},
                    {'status': 'succeeded', 'amount': Decimal('110')}],
                }, scenario)

    @with_transaction()
    def test_wrong_amount_needs_review(self):
        "The operator confirmed another amount: flagged, never booked"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Order = pool.get('restaurant.order')

        def scenario(restaurant, table, account):
            order = self._order(restaurant, table)
            tx, = Tx.create([{
                        'restaurant_order': order.id, 'account': account.id,
                        'amount': Decimal('50')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertTrue(tx.orphan)
            self.assertEqual(len(Order(order.id).payments), 0)

        self._scenario({
                'create': {'checkout_url': 'https://pay.example/r2'},
                'check': [{'status': 'succeeded', 'amount': Decimal('49')}],
                }, scenario)

    @with_transaction()
    def test_amount_above_balance_refused(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(restaurant, table, account):
            order = self._order(restaurant, table)
            tx, = Tx.create([{
                        'restaurant_order': order.id, 'account': account.id,
                        'amount': Decimal('500')}])
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])

        self._scenario({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_mobile_payment_cannot_be_typed(self):
        "Nobody can record a mobile payment by hand"
        Payment = Pool().get('restaurant.payment')

        def scenario(restaurant, table, account):
            order = self._order(restaurant, table)
            with self.assertRaises(OrderError):
                Payment.create([{
                            'order': order.id, 'method': 'mobile',
                            'amount': Decimal('10')}])
            # other methods are not affected
            Payment.create([{
                        'order': order.id, 'method': 'voucher',
                        'amount': Decimal('10')}])

        self._scenario({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_one_target(self):
        "A transaction pays one document only"
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(restaurant, table, account):
            with self.assertRaises(MobilePaymentError):
                Tx.create([{'account': account.id,
                            'amount': Decimal('10'), 'order_ref': 'X1'}])

        self._scenario({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_wizard_defaults(self):
        "The mobile payment wizard proposes the balance of the order"
        Wizard = Pool().get('mobile.payment.pay', type='wizard')

        def scenario(restaurant, table, account):
            order = self._order(restaurant, table)
            defaults = Wizard._target_start('restaurant.order', order.id)
            self.assertEqual(defaults['amount'], Decimal('110'))
            self.assertEqual(
                Wizard._target_values('restaurant.order', order.id),
                {'restaurant_order': order.id})
            with self.assertRaises(MobilePaymentError):
                Wizard._target_start('party.party', 1)

        self._scenario({'create': {}, 'check': []}, scenario)


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            RestaurantMobilePaymentTestCase))
    return suite
