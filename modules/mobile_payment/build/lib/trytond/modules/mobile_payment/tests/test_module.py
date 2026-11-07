# This file is part of the Tryton mobile_payment module.
import datetime
import unittest
from decimal import Decimal
from unittest.mock import patch

from trytond.modules.company.tests import (
    CompanyTestMixin, set_company)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction

from ..exceptions import MobilePaymentError
from ..tools import normalize_phone
from .fixture import MobileFixture


def fake_provider(results):
    """Patch the Account class with a 'fake' provider whose answers are
    taken from the `results` dict: {'create': dict, 'check': [dicts]}."""
    from ..account import Account

    def _create(self, tx):
        return results['create']

    def _check(self, tx):
        return results['check'].pop(0)

    def _lookup(self, tx):
        return results['lookup'].pop(0)

    extra = ()
    if 'lookup' in results:
        extra = (patch.object(Account, '_fake_lookup', _lookup, create=True),)
    return extra + (
        patch.object(Account, 'get_providers', classmethod(
                lambda cls: [(None, ''), ('fake', "Fake")])),
        patch.object(Account, '_fake_create', _create, create=True),
        patch.object(Account, '_fake_check', _check, create=True))


class MobilePaymentTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test mobile_payment module"
    module = 'mobile_payment'

    def test_qr_png(self):
        from ..tools import qr_png
        self.assertIsNone(qr_png(None))
        data = qr_png('https://pay.example/abc')
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_normalize_phone(self):
        self.assertEqual(normalize_phone('+509 3712-3456'), '50937123456')
        self.assertEqual(normalize_phone('37123456'), '50937123456')
        self.assertEqual(normalize_phone('00509 37123456'), '50937123456')
        self.assertIsNone(normalize_phone(''))
        with self.assertRaises(MobilePaymentError):
            normalize_phone('12345')

    def _run(self, results, scenario):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            patches = fake_provider(results)
            for p in patches:
                p.start()
            try:
                account = self._account(company, clearing, 'fake')
                scenario(company, point, product, account)
            finally:
                for p in patches:
                    p.stop()

    @with_transaction()
    def test_payment_confirmed(self):
        "A confirmed payment books the ticket and finishes it"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': sale.amount_to_pay}])
            self.assertEqual(tx.state, 'draft')
            self.assertEqual(tx.provider, 'fake')
            self.assertTrue(tx.order_ref)
            Tx.request([tx])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'pending')
            self.assertEqual(tx.checkout_url, 'https://pay.example/1')
            self.assertTrue(tx.expires_at)
            # still waiting
            Tx.check([tx])
            self.assertEqual(Tx(tx.id).state, 'pending')
            # the ticket cannot be cancelled while waiting
            with self.assertRaises(MobilePaymentError):
                Sale.cancel([Sale(sale.id)])
            # paid
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertFalse(tx.orphan)
            self.assertEqual(tx.payer, '50937123456')
            sale = Sale(sale.id)
            self.assertEqual(sale.state, 'done')
            payment, = sale.payments
            self.assertEqual(payment.amount, Decimal('100'))
            self.assertEqual(payment, tx.payment)
            self.assertTrue(payment.method.mobile)
            # a second verification changes nothing
            Tx.check([tx])
            self.assertEqual(len(Sale(sale.id).payments), 1)

        self._run({
                'create': {'checkout_url': 'https://pay.example/1',
                    'provider_ref': 'T1', 'message': 'ok'},
                'check': [
                    {'status': 'pending'},
                    {'status': 'succeeded', 'amount': Decimal('100'),
                        'payer': '50937123456', 'provider_ref': 'T1'}],
                }, scenario)

    @with_transaction()
    def test_amount_mismatch_needs_review(self):
        "An amount different from the request is flagged, not booked"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertTrue(tx.orphan)
            self.assertFalse(tx.payment)
            self.assertEqual(Sale(sale.id).state, 'open')
            Tx.resolve([tx])
            self.assertFalse(Tx(tx.id).orphan)

        self._run({
                'create': {'checkout_url': 'https://pay.example/2'},
                'check': [{'status': 'succeeded', 'amount': Decimal('90')}],
                }, scenario)

    @with_transaction()
    def test_failure_and_cancel(self):
        "Failed and cancelled requests"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'failed')
            # a failed request can be retried with a new order id
            tx2, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            self.assertNotEqual(tx2.order_ref, tx.order_ref)
            Tx.cancel([tx2])
            self.assertEqual(Tx(tx2.id).state, 'cancelled')
            Tx.delete([Tx(tx2.id)])
            with self.assertRaises(MobilePaymentError):
                Tx.delete([Tx(tx.id)])

        self._run({
                'create': {'checkout_url': 'https://pay.example/3'},
                'check': [{'status': 'failed', 'message': 'refused'}],
                }, scenario)

    @with_transaction()
    def test_amount_too_high(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('500')}])
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])

        self._run({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_manual_mobile_payment_forbidden(self):
        "The cashier cannot type a payment on a mobile method"
        pool = Pool()
        Payment = pool.get('sale.point.payment')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            from trytond.model.exceptions import DomainValidationError
            with self.assertRaises(DomainValidationError):
                Payment.create([{
                            'sale': sale.id, 'method': account.method.id,
                            'amount': Decimal('100')}])

        self._run({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_reconcile(self):
        "The scheduler confirms paid requests and expires old ones"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')

        def scenario(company, point, product, account):
            paid = self._sale(company, point, product)
            late = self._sale(company, point, product)
            txs = Tx.create([
                    {'sale': paid.id, 'account': account.id,
                        'amount': Decimal('100')},
                    {'sale': late.id, 'account': account.id,
                        'amount': Decimal('100')}])
            Tx.request(txs)
            Tx.write([Tx(txs[1].id)], {
                    'expires_at': datetime.datetime.now()
                    - datetime.timedelta(minutes=1)})
            self.assertEqual(Tx.reconcile(), 2)
            self.assertEqual(Tx(txs[0].id).state, 'succeeded')
            self.assertEqual(Tx(txs[1].id).state, 'expired')
            self.assertEqual(Sale(paid.id).state, 'done')
            self.assertEqual(Sale(late.id).state, 'open')
            # paid after expiry: the verification still books it
            Tx.check([Tx(txs[1].id)])
            self.assertEqual(Tx(txs[1].id).state, 'succeeded')

        self._run({
                'create': {'checkout_url': 'https://pay.example/4'},
                'check': [
                    {'status': 'succeeded', 'amount': Decimal('100')},
                    {'status': 'pending'},
                    {'status': 'succeeded', 'amount': Decimal('100')}],
                }, scenario)

    USSD = {'ussd_enabled': True, 'merchant_code': '12345'}

    def _ussd_tx(self, company, point, product, account, **values):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        sale = self._sale(company, point, product)
        tx, = Tx.create([dict({
                        'sale': sale.id, 'account': account.id,
                        'amount': sale.amount_to_pay, 'channel': 'ussd'},
                    **values)])
        Tx.request([tx])
        return Tx(tx.id), sale

    def test_ussd_helpers(self):
        from ..tools import (
            check_push_url, template_is_valid, tel_uri, whole_amount)
        self.assertEqual(tel_uri('*202*1*100#'), 'tel:*202*1*100%23')
        self.assertTrue(template_is_valid('*202*{merchant}*{amount}#'))
        self.assertFalse(template_is_valid('*202*{pin}#'))
        self.assertFalse(template_is_valid('202{merchant}'))
        self.assertEqual(whole_amount(Decimal('150')), '150')
        with self.assertRaises(MobilePaymentError):
            whole_amount(Decimal('150.50'))
        self.assertTrue(check_push_url('https://ntfy.sh/shop'))
        for url in ('http://ntfy.sh/x', 'https://localhost/x',
                'https://127.0.0.1/x', 'https://10.0.0.5/x',
                'https://intranet/x', 'https://srv.internal/x', ''):
            self.assertFalse(check_push_url(url), url)

    @with_transaction()
    def test_ussd_account_validation(self):
        "The USSD settings are checked when the account is saved"
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            patches = fake_provider({})
            for p in patches:
                p.start()
            try:
                with self.assertRaises(MobilePaymentError):
                    self._account(
                        company, clearing, 'fake', ussd_enabled=True,
                        ussd_menu='202', merchant_code='12345')
                with self.assertRaises(MobilePaymentError):
                    self._account(
                        company, clearing, 'fake', name="B",
                        ussd_enabled=True, ussd_menu='*202#',
                        merchant_code='x')
                with self.assertRaises(MobilePaymentError):
                    self._account(
                        company, clearing, 'fake', name="C",
                        ussd_enabled=True, ussd_menu='*202#',
                        merchant_code='12345', ussd_template='*1*{pin}#')
                account = self._account(
                    company, clearing, 'fake', name="D", ussd_enabled=True,
                    ussd_menu='*202#', merchant_code='12345',
                    ussd_template='*202*{merchant}*{amount}#')
                self.assertEqual(
                    account.ussd_dial(Decimal('150')), '*202*12345*150#')
                with self.assertRaises(MobilePaymentError):
                    account.ussd_dial(Decimal('150.5'))
            finally:
                for p in patches:
                    p.stop()

    @with_transaction()
    def test_ussd_request_needs_enabled_account(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100'), 'channel': 'ussd'}])
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])

        self._run({'create': {}}, scenario)

    def _ussd_account(self, company, clearing, **values):
        values = dict(self.USSD, ussd_menu='*202#', **values)
        return self._account(company, clearing, 'fake', **values)

    def _ussd_run(self, results, scenario, **values):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            patches = fake_provider(results)
            for p in patches:
                p.start()
            try:
                account = self._ussd_account(company, clearing, **values)
                scenario(company, point, product, account)
            finally:
                for p in patches:
                    p.stop()

    @with_transaction()
    def test_ussd_menu_and_qr(self):
        "A USSD request shows the steps and a dial link, no operator call"
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            self.assertEqual(tx.state, 'pending')
            self.assertEqual(tx.ussd_code, '*202#')
            self.assertEqual(tx.ussd_uri, 'tel:*202%23')
            self.assertIn('12345', tx.instructions)
            self.assertIn('100', tx.instructions)
            self.assertFalse(tx.checkout_url)
            self.assertTrue(tx.expires_at)
            # no phone: no share link
            self.assertIsNone(tx.share_whatsapp)
            Tx.write([tx], {'phone': '50937123456'})
            tx = Tx(tx.id)
            self.assertTrue(tx.share_whatsapp.startswith(
                    'https://wa.me/50937123456?text='))
            self.assertTrue(tx.share_sms.startswith('sms:+50937123456?body='))
            # the verification of the operator is not used for USSD
            Tx.check([tx])
            self.assertEqual(Tx(tx.id).state, 'pending')

        self._ussd_run({'create': {}}, scenario)

    @with_transaction()
    def test_ussd_shortcut(self):
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            self.assertEqual(tx.ussd_code, '*202*12345*100#')

        self._ussd_run(
            {'create': {}}, scenario,
            ussd_template='*202*{merchant}*{amount}#')

    @with_transaction()
    def test_ussd_confirmed_by_operator(self):
        "A receipt confirmed by the operator books the ticket"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            with self.assertRaises(MobilePaymentError):  # no receipt
                Tx.confirm_ussd([tx])
            Tx.write([tx], {'receipt': 'R-1'})
            Tx.confirm_ussd([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertFalse(tx.manual)
            self.assertFalse(tx.orphan)
            self.assertEqual(tx.payer, '50937123456')
            self.assertTrue(tx.confirmed_by)
            self.assertEqual(Sale(sale.id).state, 'done')

        self._ussd_run({
                'create': {},
                'lookup': [{'status': 'succeeded', 'amount': Decimal('100'),
                        'payer': '50937123456', 'provider_ref': 'R-1'}],
                }, scenario)

    @with_transaction()
    def test_ussd_receipt_refused(self):
        "Unpaid, foreign or reused receipts never book a payment"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            Tx.write([tx], {'receipt': 'R-2'})
            with self.assertRaises(MobilePaymentError):  # not paid
                Tx.confirm_ussd([Tx(tx.id)])
            with self.assertRaises(MobilePaymentError):  # other order
                Tx.confirm_ussd([Tx(tx.id)])
            Tx.confirm_ussd([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'succeeded')
            # the same receipt cannot pay a second ticket
            tx2, sale2 = self._ussd_tx(company, point, product, account)
            with self.assertRaises(MobilePaymentError):
                Tx(tx2.id)._confirm_ussd('R-2')
            self.assertEqual(Tx(tx2.id).state, 'pending')

        self._ussd_run({
                'create': {},
                'lookup': [
                    {'status': 'pending', 'message': 'not paid'},
                    {'status': 'succeeded', 'reference': 'OTHER',
                        'amount': Decimal('100')},
                    {'status': 'succeeded', 'amount': Decimal('100')}],
                }, scenario)

    @with_transaction()
    def test_ussd_manual_confirmation(self):
        "Without operator answer, only an admin may confirm, flagged to review"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        User = pool.get('res.user')
        Sale = pool.get('sale.point.sale')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            Tx.write([tx], {'receipt': 'R-3'})
            with patch.object(User, 'get_groups', classmethod(
                        lambda cls: [])):
                with self.assertRaises(MobilePaymentError):
                    Tx.confirm_ussd([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'pending')
            Tx.confirm_ussd([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertTrue(tx.manual)
            self.assertTrue(tx.orphan)
            self.assertEqual(Sale(sale.id).state, 'done')

        self._ussd_run({
                'create': {}, 'lookup': [
                    {'status': 'unknown'}, {'status': 'unknown'}]},
            scenario, allow_manual=True)

    @with_transaction()
    def test_ussd_manual_not_allowed(self):
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            Tx.write([tx], {'receipt': 'R-4'})
            with self.assertRaises(MobilePaymentError):
                Tx.confirm_ussd([Tx(tx.id)])

        self._ussd_run({'create': {}, 'lookup': [{'status': 'unknown'}]},
            scenario)

    @with_transaction()
    def test_ussd_expires_but_confirmable(self):
        Tx = Pool().get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            tx, sale = self._ussd_tx(company, point, product, account)
            Tx.write([tx], {'expires_at': datetime.datetime.now()
                    - datetime.timedelta(minutes=1)})
            Tx.reconcile()
            self.assertEqual(Tx(tx.id).state, 'expired')
            Tx.write([Tx(tx.id)], {'receipt': 'R-5'})
            Tx.confirm_ussd([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'succeeded')

        self._ussd_run({'create': {}, 'lookup': [
                    {'status': 'succeeded', 'amount': Decimal('100')}]},
            scenario)

    @with_transaction()
    def test_ussd_wizard(self):
        pool = Pool()
        Pay = pool.get('mobile.payment.pay', type='wizard')
        Tx = pool.get('mobile.payment.transaction')
        Sale = pool.get('sale.point.sale')
        from trytond.transaction import Transaction

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            session_id, _, _ = Pay.create()
            with Transaction().set_context(
                    active_model='sale.point.sale', active_id=sale.id):
                defaults = Pay(session_id).default_start([])
            self.assertEqual(defaults['channel'], 'link')
            wizard = Pay(session_id)
            wizard.start.account = account
            wizard.start.amount = Decimal('100')
            wizard.start.phone = None
            wizard.start.channel = 'ussd'
            wizard.start.target_model = 'sale.point.sale'
            wizard.start.target_id = sale.id
            self.assertEqual(wizard.transition_request(), 'result')
            tx = Tx(wizard.start.transaction_id)
            self.assertEqual(tx.channel, 'ussd')
            result = wizard.default_result([])
            self.assertEqual(result['ussd_code'], '*202#')
            self.assertTrue(result['qr_code'])
            wizard.result.receipt = 'R-6'
            self.assertEqual(wizard.transition_verify(), 'result')
            self.assertEqual(Tx(tx.id).state, 'succeeded')
            self.assertEqual(Tx(tx.id).receipt, 'R-6')

        self._ussd_run({'create': {}, 'lookup': [
                    {'status': 'succeeded', 'amount': Decimal('100')}]},
            scenario)

    @with_transaction()
    def test_push(self):
        "Events are queued to matching targets; errors never escape"
        pool = Pool()
        Push = pool.get('mobile.payment.push')
        company = self._company()
        with set_company(company):
            with self.assertRaises(MobilePaymentError):
                Push.create([{
                            'company': company.id, 'name': "x", 'kind': 'ntfy',
                            'url': 'http://ntfy.sh/shop'}])
            ntfy, hook = Push.create([
                    {'company': company.id, 'name': "ntfy", 'kind': 'ntfy',
                        'url': 'https://ntfy.sh/shop', 'token': 'tk',
                        'events': ['succeeded', 'manual']},
                    {'company': company.id, 'name': "hook",
                        'kind': 'webhook',
                        'url': 'https://hooks.example.com/pay',
                        'events': ['failed']}])
            calls = []

            def fake_http(url, data=None, headers=None, **kw):
                calls.append((url, data, headers))
                return 200, None

            with patch('trytond.modules.mobile_payment.push.http_json',
                    fake_http):
                self.assertEqual(
                    ntfy.send('succeeded', "T", "B", 9), 'OK')
                url, data, headers = calls[0]
                self.assertEqual(url, 'https://ntfy.sh/')
                self.assertEqual(data['topic'], 'shop')
                self.assertEqual(data['priority'], 5)
                self.assertEqual(headers['Authorization'], 'Bearer tk')
                self.assertEqual(hook.send('failed', "T", "B", 3, {'a': 1}),
                    'OK')
                self.assertEqual(calls[1][0], 'https://hooks.example.com/pay')
                self.assertEqual(calls[1][1]['data'], {'a': 1})
            # an unreachable server is recorded, not raised
            with patch('trytond.modules.mobile_payment.push.http_json',
                    side_effect=MobilePaymentError("down")):
                self.assertIn('down', Push(ntfy.id).send('failed', "T", "B"))
            self.assertEqual(Push(ntfy.id).last_result[:4], 'down')
            # only the targets that listen to the event are queued
            Queue = pool.get('ir.queue')

            def queued(event):
                before = Queue.search_count([])
                Push.enqueue(company, event, "T", "B")
                return Queue.search_count([]) - before

            # the queue runs after the commit: one entry per listening target
            base = queued('orphan')                   # nobody listens
            self.assertEqual(queued('failed') - base, 1)     # webhook only
            self.assertEqual(queued('succeeded') - base, 1)  # ntfy only

    @with_transaction()
    def test_wizard(self):
        "The cashier wizard requests then verifies"
        pool = Pool()
        Sale = pool.get('sale.point.sale')
        Tx = pool.get('mobile.payment.transaction')
        Wizard = pool.get('mobile.payment.pay', type='wizard')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            session_id, start_state, _ = Wizard.create()
            self.assertEqual(start_state, 'start')
            from trytond.transaction import Transaction as T
            with T().set_context(active_id=sale.id, active_ids=[sale.id],
                    active_model='sale.point.sale'):
                data = Wizard.execute(session_id, {}, 'start')
                defaults = {k: v for k, v in data['view']['defaults'].items()
                    if '.' not in k}
                self.assertEqual(defaults['account'], account.id)
                self.assertEqual(defaults['amount'], Decimal('100'))
                data = Wizard.execute(session_id, {
                        'start': dict(defaults, phone='3712 3456')},
                    'request')
                self.assertEqual(
                    data['view']['defaults']['state'], 'pending')
                # the link is shown as a QR code (PNG) for the customer
                qr = data['view']['defaults']['qr_code']
                self.assertTrue(bytes(qr).startswith(b'\x89PNG'))
                tx, = Tx.search([('sale', '=', sale.id)])
                self.assertEqual(tx.phone, '50937123456')
                data = Wizard.execute(session_id, {
                        'start': dict(defaults, phone='3712 3456'),
                        'result': {k: v for k, v in data['view']['defaults'].items()
                            if '.' not in k}}, 'verify')
                self.assertEqual(data['view']['defaults']['state'],
                    'succeeded')
                # nothing left to scan once paid
                self.assertFalse(data['view']['defaults']['qr_code'])
            self.assertEqual(Sale(sale.id).state, 'done')

        self._run({
                'create': {'checkout_url': 'https://pay.example/5'},
                'check': [{'status': 'succeeded', 'amount': Decimal('100')}],
                }, scenario)

    @with_transaction()
    def test_handle_return(self):
        "The return URL only triggers a server-side verification"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')

        def scenario(company, point, product, account):
            sale = self._sale(company, point, product)
            tx, = Tx.create([{
                        'sale': sale.id, 'account': account.id,
                        'amount': Decimal('100')}])
            Tx.request([tx])
            # forged parameters do not pay anything: the operator says no
            self.assertEqual(Tx.handle_return('fake', {'x': '1'}), 0)
            self.assertEqual(Tx(tx.id).state, 'pending')
            self.assertEqual(Tx.handle_return('fake', {}), 1)
            self.assertEqual(Tx(tx.id).state, 'succeeded')

        self._run({
                'create': {'checkout_url': 'https://pay.example/6'},
                'check': [
                    {'status': 'pending'},
                    {'status': 'succeeded', 'amount': Decimal('100')}],
                }, scenario)


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            MobilePaymentTestCase))
    return suite
