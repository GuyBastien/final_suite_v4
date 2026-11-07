# This file is part of the Tryton natcash module.
import contextlib
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from natcashify.core.testing import FakeTransport, json_response, sign_callback

from trytond.modules.company.tests import CompanyTestMixin, set_company
from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tests.fixture import MobileFixture
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction

CREDENTIALS = dict(
    natcash_partner_code='PARTNER', natcash_function_code='FUNC',
    natcash_username='user', natcash_password='pass',
    natcash_private_key='KEY',
    natcash_callback_url='https://erp.example/db/mobile_payment/natcash/return')


def tx_response(order, status=0, code=1, amount=100):
    return json_response({
            'status': status,
            'data': {
                'orderNumber': order, 'transId': 'N77', 'amount': amount,
                'toPhone': '50937123456', 'responseCode': code}})


class NatCashTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test natcash module"
    module = 'natcash'

    @contextlib.contextmanager
    def _setup(self, transport, **values):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            account = self._account(
                company, clearing, 'natcash', **dict(CREDENTIALS, **values))
            with patch(
                    'trytond.modules.natcash.account.Account.'
                    '_natcash_transport', lambda self: transport):
                yield company, point, product, account

    def _tx(self, company, point, product, account, phone=None):
        Tx = Pool().get('mobile.payment.transaction')
        sale = self._sale(company, point, product)
        tx, = Tx.create([{
                    'sale': sale.id, 'account': account.id,
                    'amount': Decimal('100'), 'phone': phone}])
        return sale, tx

    @with_transaction()
    def test_create_check_and_pay(self):
        Tx = Pool().get('mobile.payment.transaction')
        Sale = Pool().get('sale.point.sale')
        transport = FakeTransport()
        with self._setup(transport) as (company, point, product, account):
            sale, tx = self._tx(
                company, point, product, account, phone='50937123456')
            transport._responses.extend([
                json_response({
                        'status': 0, 'url': 'https://pay.natcash.example/c/1',
                        'expiredAt': 600}),
                json_response({'status': 101, 'message': 'not found'}),
                tx_response(tx.order_ref)])
            Tx.request([tx])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'pending')
            self.assertEqual(tx.checkout_url, 'https://pay.natcash.example/c/1')
            body = json.loads(transport.requests[0].body)
            self.assertEqual(body['orderNumber'], tx.order_ref)
            self.assertEqual(body['amount'], 100)
            self.assertEqual(body['msisdn'], '50937123456')
            self.assertTrue(body['skipPhoneInput'])
            self.assertEqual(body['partnerCode'], 'PARTNER')
            self.assertTrue(transport.requests[0].url.startswith(
                    'https://testmerchantpay.natcom.com.ht/'))
            Tx.check([tx])
            self.assertEqual(Tx(tx.id).state, 'pending')
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertEqual(tx.provider_ref, 'N77')
            self.assertEqual(Sale(sale.id).state, 'done')

    @with_transaction()
    def test_declined(self):
        Tx = Pool().get('mobile.payment.transaction')
        transport = FakeTransport()
        with self._setup(transport) as (company, point, product, account):
            sale, tx = self._tx(company, point, product, account)
            transport._responses.extend([
                json_response({
                        'status': 0, 'url': 'https://pay.natcash.example/c/2',
                        'expiredAt': 600}),
                tx_response(tx.order_ref, code=-1)])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'failed')
            # phone page is not skipped when the phone is unknown
            self.assertFalse(
                json.loads(transport.requests[0].body)['skipPhoneInput'])

    @with_transaction()
    def test_wrong_order_in_answer(self):
        "An answer about another order is rejected by the SDK"
        Tx = Pool().get('mobile.payment.transaction')
        transport = FakeTransport()
        with self._setup(transport) as (company, point, product, account):
            sale, tx = self._tx(company, point, product, account)
            transport._responses.extend([
                json_response({
                        'status': 0, 'url': 'https://pay.natcash.example/c/3',
                        'expiredAt': 600}),
                tx_response('OTHER-1')])
            Tx.request([tx])
            with self.assertRaises(MobilePaymentError):
                Tx.check([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'pending')

    @with_transaction()
    def test_callback_signature(self):
        "Forged callbacks are rejected, signed ones trigger a verification"
        Tx = Pool().get('mobile.payment.transaction')
        Sale = Pool().get('sale.point.sale')
        transport = FakeTransport()
        with self._setup(transport) as (company, point, product, account):
            sale, tx = self._tx(company, point, product, account)
            transport._responses.extend([
                json_response({
                        'status': 0, 'url': 'https://pay.natcash.example/c/4',
                        'expiredAt': 600}),
                tx_response(tx.order_ref)])
            Tx.request([tx])
            with self.assertRaises(MobilePaymentError):
                Tx.handle_return('natcash', {
                        'orderNumber': tx.order_ref, 'code': '1',
                        'signature': 'forged'})
            self.assertEqual(Tx(tx.id).state, 'pending')
            config = account._natcash_client().config
            signature = sign_callback(config, tx.order_ref, 1)
            self.assertEqual(Tx.handle_return('natcash', {
                        'orderNumber': tx.order_ref, 'code': '1',
                        'signature': signature}), 1)
            self.assertEqual(Sale(sale.id).state, 'done')

    @with_transaction()
    def test_settings_validation(self):
        Tx = Pool().get('mobile.payment.transaction')
        with self._setup(
                FakeTransport(), natcash_callback_url='not-a-url') as (
                company, point, product, account):
            sale, tx = self._tx(company, point, product, account)
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])

    @with_transaction()
    def test_only_gourdes(self):
        Tx = Pool().get('mobile.payment.transaction')
        company = self._company('USD')
        with set_company(company):
            point, product, clearing = self._pos(company)
            account = self._account(
                company, clearing, 'natcash', **CREDENTIALS)
            sale, tx = self._tx(company, point, product, account)
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])
    @with_transaction()
    def test_ussd_manual_only(self):
        "NatCash has no receipt lookup: manual confirmation, flagged"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        transport = FakeTransport([])
        with self._setup(
                transport, ussd_enabled=True, ussd_menu='*202#',
                merchant_code='888', allow_manual=False) as (
                company, point, product, account):
            self.assertFalse(account.provider_supports('lookup'))
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100'), 'channel': 'ussd'}])
            Tx.request([tx])
            Tx.write([Tx(tx.id)], {'receipt': 'N-1'})
            with self.assertRaises(MobilePaymentError):
                Tx.confirm_ussd([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'pending')
            pool.get('mobile.payment.account').write(
                [account], {'allow_manual': True})
            Tx.confirm_ussd([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertTrue(tx.manual)
            self.assertTrue(tx.orphan)


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            NatCashTestCase))
    return suite
