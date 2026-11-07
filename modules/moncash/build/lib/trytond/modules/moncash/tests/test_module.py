# This file is part of the Tryton moncash module.
import base64
import contextlib
import unittest
from decimal import Decimal
from unittest.mock import patch

from trytond.modules.company.tests import CompanyTestMixin, set_company
from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tests.fixture import MobileFixture
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction

TOKEN = (200, {'access_token': 'TOK', 'token_type': 'bearer'})


class Http:
    "Queue of canned answers, recording the calls."

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.answers.pop(0)


class MonCashTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test moncash module"
    module = 'moncash'

    @contextlib.contextmanager
    def _setup(self, **values):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            values.setdefault('moncash_client_id', 'CID')
            values.setdefault('moncash_client_secret', 'SECRET')
            account = self._account(company, clearing, 'moncash', **values)
            yield company, point, product, account

    @with_transaction()
    def test_create_and_pay(self):
        "Full cycle against a faked MonCash"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            account = self._account(
                company, clearing, 'moncash', moncash_client_id='CID',
                moncash_client_secret='SECRET')
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            http = Http(
                TOKEN,
                (202, {'path': '/v1/CreatePayment', 'status': 202,
                        'payment_token': {'token': 'PT123'}, 'mode': 'sandbox'}),
                TOKEN, (404, {'message': 'not found'}),
                TOKEN, (200, {'payment': {
                            'reference': tx.order_ref,
                            'transaction_id': '4412',
                            'cost': 100, 'message': 'successful',
                            'payer': '50937123456'}, 'status': 200}))
            with patch('trytond.modules.moncash.account.http_json', http):
                Tx.request([tx])
                tx = Tx(tx.id)
                self.assertEqual(
                    tx.checkout_url,
                    'https://sandbox.moncashbutton.digicelgroup.com/'
                    'Moncash-middleware/Payment/Redirect?token=PT123')
                url, kw = http.calls[0]
                self.assertEqual(
                    url, 'https://sandbox.moncashbutton.digicelgroup.com/'
                    'Api/oauth/token')
                self.assertEqual(
                    kw['headers']['Authorization'],
                    'Basic ' + base64.b64encode(b'CID:SECRET').decode())
                self.assertEqual(
                    kw['data'], 'scope=read,write&grant_type=client_credentials')
                url, kw = http.calls[1]
                self.assertTrue(url.endswith('/v1/CreatePayment'))
                self.assertEqual(kw['headers']['Authorization'], 'Bearer TOK')
                self.assertEqual(
                    kw['data'], {'amount': 100, 'orderId': tx.order_ref})
                # not paid yet (MonCash does not know the order)
                Tx.check([tx])
                self.assertEqual(Tx(tx.id).state, 'pending')
                # paid
                Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertEqual(tx.provider_ref, '4412')
            self.assertEqual(tx.payer, '50937123456')
            self.assertEqual(Sale(sale.id).state, 'done')

    @with_transaction()
    def test_live_hosts(self):
        http = Http(TOKEN, (202, {'payment_token': {'token': 'X'}}))
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        with self._setup(sandbox=False) as (company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            with patch('trytond.modules.moncash.account.http_json', http):
                Tx.request([tx])
            self.assertTrue(http.calls[0][0].startswith(
                    'https://moncashbutton.digicelgroup.com/Api/'))
            self.assertIn(
                '//moncashbutton.digicelgroup.com/Moncash-middleware/',
                Tx(tx.id).checkout_url)

    @with_transaction()
    def test_only_gourdes(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        company = self._company('USD')
        with set_company(company):
            point, product, clearing = self._pos(company)
            account = self._account(
                company, clearing, 'moncash', moncash_client_id='CID',
                moncash_client_secret='SECRET')
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            with patch('trytond.modules.moncash.account.http_json',
                    Http()):
                with self.assertRaises(MobilePaymentError):
                    Tx.request([tx])

    @with_transaction()
    def test_bad_credentials(self):
        pool = Pool()
        Account = pool.get('mobile.payment.account')
        with self._setup() as (company, point, product, account):
            with patch('trytond.modules.moncash.account.http_json',
                    Http((401, {'error': 'invalid_client'}))):
                Account.test_connection([account])
            self.assertIn('refused', Account(account.id).last_test)
            with patch('trytond.modules.moncash.account.http_json',
                    Http(TOKEN)):
                Account.test_connection([Account(account.id)])
            self.assertIn('sandbox', Account(account.id).last_test)

    @with_transaction()
    def test_secret_hidden_from_cashier(self):
        "A cashier can use the account but cannot read the secret key"
        from trytond.exceptions import UserError
        from trytond.model.exceptions import AccessError
        from trytond.transaction import Transaction
        pool = Pool()
        Account = pool.get('mobile.payment.account')
        User = pool.get('res.user')
        ModelData = pool.get('ir.model.data')
        with self._setup() as (company, point, product, account):
            group = ModelData.get_id('sale_point', 'group_sale_point')
            admin_group = ModelData.get_id(
                'mobile_payment', 'group_mobile_payment_admin')
            cashier, manager = User.create([
                {'name': "Cashier", 'login': 'cashier',
                    'groups': [('add', [group])],
                    'companies': [('add', [company.id])],
                    'company': company.id},
                {'name': "Manager", 'login': 'manager',
                    'groups': [('add', [group, admin_group])],
                    'companies': [('add', [company.id])],
                    'company': company.id}])
            with Transaction().set_user(cashier.id), \
                    Transaction().set_context(_check_access=True):
                self.assertEqual(
                    Account.read([account.id], ['name'])[0]['name'], "Mobile")
                with self.assertRaises(AccessError):
                    Account.read([account.id], ['moncash_client_secret'])
            with Transaction().set_user(manager.id), \
                    Transaction().set_context(_check_access=True):
                self.assertEqual(Account.read(
                        [account.id], ['moncash_client_secret'])[0][
                        'moncash_client_secret'], 'SECRET')

    @with_transaction()
    def test_result_mapping(self):
        Account = Pool().get('mobile.payment.account')
        map_ = Account._moncash_result
        self.assertEqual(map_(404, None), {'status': 'pending'})
        self.assertEqual(map_(200, {}), {'status': 'pending'})
        done = map_(200, {'payment': {
                    'transaction_id': 9, 'cost': '125.50',
                    'message': 'Successful', 'payer': '509'}})
        self.assertEqual(done['status'], 'succeeded')
        self.assertEqual(done['amount'], Decimal('125.50'))
        # anything but "successful" is never a success
        other = map_(200, {'payment': {'message': 'failed'}})
        self.assertEqual(other['status'], 'pending')
        with self.assertRaises(MobilePaymentError):
            map_(500, None)

    @with_transaction()
    def test_return_parameters(self):
        with self._setup() as (company, point, product, account):
            self.assertEqual(
                account._moncash_parse_return({'orderId': 'A-1'}), 'A-1')
            self.assertIsNone(account._moncash_parse_return({}))
            http = Http(TOKEN, (200, {'payment': {'reference': 'B-2'}}))
            with patch('trytond.modules.moncash.account.http_json', http):
                self.assertEqual(account._moncash_parse_return(
                        {'transactionId': '77'}), 'B-2')
            self.assertTrue(http.calls[1][0].endswith(
                    '/v1/RetrieveTransactionPayment'))
    @with_transaction()
    def test_ussd_receipt_lookup(self):
        "The receipt typed by the cashier is verified by MonCash"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        with self._setup(
                ussd_enabled=True, merchant_code='777', ussd_menu='*202#',
                allow_manual=False) as (company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100'), 'channel': 'ussd'}])
            Tx.request([tx])
            tx = Tx(tx.id)
            self.assertEqual(tx.ussd_code, '*202#')
            Tx.write([tx], {'receipt': '5551'})
            # unknown to MonCash and no manual fallback: refused
            http = Http(TOKEN, (404, {'message': 'not found'}))
            with patch('trytond.modules.moncash.account.http_json', http):
                with self.assertRaises(MobilePaymentError):
                    Tx.confirm_ussd([Tx(tx.id)])
            self.assertTrue(http.calls[1][0].endswith(
                    '/v1/RetrieveTransactionPayment'))
            self.assertEqual(http.calls[1][1]['data'],
                {'transactionId': '5551'})
            # a receipt of another order
            http = Http(TOKEN, (200, {'payment': {
                            'reference': 'OTHER', 'transaction_id': '5551',
                            'cost': 100, 'message': 'successful'}}))
            with patch('trytond.modules.moncash.account.http_json', http):
                with self.assertRaises(MobilePaymentError):
                    Tx.confirm_ussd([Tx(tx.id)])
            # the right one
            http = Http(TOKEN, (200, {'payment': {
                            'reference': tx.order_ref,
                            'transaction_id': '5551', 'cost': 100,
                            'message': 'successful',
                            'payer': '50937123456'}}))
            with patch('trytond.modules.moncash.account.http_json', http):
                Tx.confirm_ussd([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertFalse(tx.manual)
            self.assertEqual(tx.provider_ref, '5551')

    @with_transaction()
    def test_lookup_mapping(self):
        with self._setup() as (company, point, product, account):
            class T:
                receipt = '9'
            http = Http(TOKEN, (200, {}))
            with patch('trytond.modules.moncash.account.http_json', http):
                self.assertEqual(
                    account._moncash_lookup(T), {'status': 'unknown'})
            http = Http(TOKEN, (200, {'payment': {'message': 'pending'}}))
            with patch('trytond.modules.moncash.account.http_json', http):
                self.assertEqual(
                    account._moncash_lookup(T)['status'], 'pending')


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            MonCashTestCase))
    return suite
