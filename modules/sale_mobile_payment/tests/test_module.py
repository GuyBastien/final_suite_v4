# This file is part of the Tryton sale_mobile_payment module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import CompanyTestMixin, set_company
from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tests.fixture import MobileFixture
from trytond.modules.mobile_payment.tests.test_module import fake_provider
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class SaleMobilePaymentTestCase(
        MobileFixture, CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test sale_mobile_payment module"
    module = 'sale_mobile_payment'

    def _invoice(self, company, product, price='100', post=True):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        Party = pool.get('party.party')
        Account = pool.get('account.account')
        Journal = pool.get('account.journal')
        party, = Party.create([{'name': "Client"}])
        revenue, = Account.search([
                ('type.revenue', '=', True),
                ('company', '=', company.id)], limit=1)
        journal, = Journal.search([('type', '=', 'revenue')], limit=1)
        invoice, = Invoice.create([{
                    'company': company.id, 'type': 'out', 'party': party.id,
                    'currency': company.currency.id,
                    'account': party.account_receivable_used.id,
                    'journal': journal.id,
                    'invoice_address': pool.get('party.address').create(
                        [{'party': party.id}])[0].id,
                    'lines': [('create', [{
                                    'invoice_type': 'out', 'type': 'line',
                                    'description': "Service",
                                    'quantity': 1, 'unit_price': Decimal(price),
                                    'account': revenue.id}])]}])
        if post:
            Invoice.post([invoice])
        return Invoice(invoice.id)

    def _method(self, company, clearing):
        pool = Pool()
        Journal = pool.get('account.journal')
        Method = pool.get('account.invoice.payment.method')
        journal, = Journal.search([('type', '=', 'cash')], limit=1)
        party_account, = pool.get('account.account').search([
                ('type.receivable', '=', True),
                ('company', '=', company.id)], limit=1)
        method, = Method.create([{
                    'company': company.id, 'name': "Mobile",
                    'journal': journal.id,
                    'debit_account': clearing.id,
                    'credit_account': party_account.id}])
        return method

    def _run(self, results, scenario):
        company = self._company()
        with set_company(company):
            point, product, clearing = self._pos(company)
            from trytond.modules.account_invoice.tests import (
                set_invoice_sequences)
            fiscalyear, = Pool().get('account.fiscalyear').search([
                    ('company', '=', company.id)])
            set_invoice_sequences(fiscalyear)
            patches = fake_provider(results)
            for p in patches:
                p.start()
            try:
                account = self._account(company, clearing, 'fake')
                scenario(company, product, clearing, account)
            finally:
                for p in patches:
                    p.stop()

    @with_transaction()
    def test_invoice_paid_by_mobile(self):
        "A confirmed payment pays and reconciles the invoice"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Invoice = pool.get('account.invoice')

        def scenario(company, product, clearing, account):
            invoice = self._invoice(company, product)
            self.assertEqual(invoice.amount_to_pay, Decimal('100'))
            tx_values = {
                'invoice': invoice.id, 'account': account.id,
                'amount': invoice.amount_to_pay}
            tx, = Tx.create([tx_values])
            self.assertTrue(tx.order_ref.startswith(invoice.number))
            self.assertEqual(tx.document, invoice.rec_name)
            # no invoice payment method yet: refused before asking money
            with self.assertRaises(MobilePaymentError):
                Tx.request([tx])
            account.invoice_payment_method = self._method(company, clearing)
            account.save()
            Tx.request([Tx(tx.id)])
            self.assertTrue(Tx(tx.id).qr_code)
            Tx.check([Tx(tx.id)])
            tx = Tx(tx.id)
            self.assertEqual(tx.state, 'succeeded')
            self.assertFalse(tx.orphan)
            invoice = Invoice(invoice.id)
            self.assertEqual(invoice.amount_to_pay, Decimal(0))
            self.assertEqual(invoice.state, 'paid')
            self.assertEqual(invoice.mobile_transactions, (tx,))

        self._run({
                'create': {'checkout_url': 'https://pay.example/9'},
                'check': [{'status': 'succeeded', 'amount': Decimal('100')}],
                }, scenario)

    @with_transaction()
    def test_partial_payment(self):
        "A partial payment lowers the balance, the invoice stays posted"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Invoice = pool.get('account.invoice')

        def scenario(company, product, clearing, account):
            account.invoice_payment_method = self._method(company, clearing)
            account.save()
            invoice = self._invoice(company, product)
            tx, = Tx.create([{
                        'invoice': invoice.id, 'account': account.id,
                        'amount': Decimal('40')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            self.assertEqual(Tx(tx.id).state, 'succeeded')
            invoice = Invoice(invoice.id)
            self.assertEqual(invoice.amount_to_pay, Decimal('60'))
            self.assertEqual(invoice.state, 'posted')

        self._run({
                'create': {'checkout_url': 'https://pay.example/9'},
                'check': [{'status': 'succeeded', 'amount': Decimal('40')}],
                }, scenario)

    @with_transaction()
    def test_sale_pays_through_its_invoice(self):
        "A sale is paid on its single posted invoice"
        from types import SimpleNamespace as NS
        Sale = Pool().get('sale.sale')

        def scenario(company, product, clearing, account):
            invoice = self._invoice(company, product)
            sale = NS(rec_name="S1", invoices=[invoice])
            self.assertEqual(Sale._mobile_invoice(sale), invoice)
            with self.assertRaises(MobilePaymentError):
                Sale._mobile_invoice(NS(rec_name="S2", invoices=[]))
            with self.assertRaises(MobilePaymentError):
                Sale._mobile_invoice(NS(
                        rec_name="S3", invoices=[invoice, invoice]))

        self._run({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_wrong_amount_needs_review(self):
        "An amount different from the request is never booked"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Invoice = pool.get('account.invoice')

        def scenario(company, product, clearing, account):
            account.invoice_payment_method = self._method(company, clearing)
            account.save()
            invoice = self._invoice(company, product)
            tx, = Tx.create([{
                        'invoice': invoice.id, 'account': account.id,
                        'amount': Decimal('100')}])
            Tx.request([tx])
            Tx.check([Tx(tx.id)])
            self.assertTrue(Tx(tx.id).orphan)
            self.assertEqual(
                Invoice(invoice.id).amount_to_pay, Decimal('100'))

        self._run({
                'create': {'checkout_url': 'https://pay.example/9'},
                'check': [{'status': 'succeeded', 'amount': Decimal('90')}],
                }, scenario)

    @with_transaction()
    def test_draft_invoice_refused(self):
        "Only a posted invoice can be paid"
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        Wizard = pool.get('mobile.payment.pay', type='wizard')

        def scenario(company, product, clearing, account):
            account.invoice_payment_method = self._method(company, clearing)
            account.save()
            invoice = self._invoice(company, product, post=False)
            session_id, _, _ = Wizard.create()
            from trytond.transaction import Transaction as T
            with T().set_context(active_id=invoice.id,
                    active_ids=[invoice.id], active_model='account.invoice'):
                with self.assertRaises(MobilePaymentError):
                    Wizard.execute(session_id, {}, 'start')

        self._run({'create': {}, 'check': []}, scenario)

    @with_transaction()
    def test_wizard_on_invoice(self):
        "The wizard starts from the invoice with its balance"
        pool = Pool()
        Wizard = pool.get('mobile.payment.pay', type='wizard')

        def scenario(company, product, clearing, account):
            account.invoice_payment_method = self._method(company, clearing)
            account.save()
            invoice = self._invoice(company, product, price='250')
            session_id, _, _ = Wizard.create()
            from trytond.transaction import Transaction as T
            with T().set_context(active_id=invoice.id,
                    active_ids=[invoice.id], active_model='account.invoice'):
                data = Wizard.execute(session_id, {}, 'start')
                defaults = data['view']['defaults']
                self.assertEqual(defaults['amount'], Decimal('250'))
                self.assertEqual(defaults['account'], account.id)

        self._run({'create': {}, 'check': []}, scenario)


def suite():
    suite = test_tryton.suite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
            SaleMobilePaymentTestCase))
    return suite
