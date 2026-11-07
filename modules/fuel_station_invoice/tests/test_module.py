# This file is part of the Tryton fuel_station_invoice module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import create_company, set_company
from trytond.modules.fuel_station.tests.test_module import (
    FuelStationTestCase, TODAY)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction


class FuelStationInvoiceTestCase(ModuleTestCase):
    "Test fuel_station_invoice module"
    module = 'fuel_station_invoice'
    extras = []

    @with_transaction()
    def test_credit_sales_invoice(self):
        "Test credit payments create a draft customer invoice"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Type = pool.get('account.account.type')
            Account = pool.get('account.account')
            Shift = pool.get('fuel.shift')
            Party = pool.get('party.party')
            helper = FuelStationTestCase()
            ctx = helper._setup(company)
            helper._fill(company, ctx, ctx['tank1'], 5000.0)
            helper._fill(company, ctx, ctx['tank2'], 5000.0)
            ctx['station'].save()
            rtype, itype = Type.create([
                    {'name': "Receivable", 'company': company.id,
                        'statement': 'balance', 'assets': True,
                        'receivable': True},
                    {'name': "Revenue", 'company': company.id,
                        'statement': 'income', 'revenue': True}])
            receivable, revenue = Account.create([
                    {'name': "Receivable", 'company': company.id,
                        'type': rtype.id, 'party_required': True,
                        'reconcile': True},
                    {'name': "Revenue", 'company': company.id,
                        'type': itype.id}])
            customer, = Party.create([{
                        'name': "Customer",
                        'account_receivable': receivable.id,
                        'addresses': [('create', [{}])]}])
            customer = Party(customer.id)
            self.assertEqual(customer.account_receivable_used, receivable)
            product = ctx['diesel']
            Category = pool.get('product.category')
            category, = Category.create([{
                        'name': "Fuel", 'accounting': True,
                        'account_revenue': revenue.id}])
            template = product.template
            template.account_category = category
            template.save()
            shift = helper._shift(company, ctx)
            Shift.open_shift([shift])
            shift = Shift(shift.id)
            for line in shift.lines:
                line.closing_index = line.opening_index + 10
                line.save()
            Shift.write([shift], {'payments': [('create', [
                            {'method': 'credit', 'amount': Decimal('30'),
                                'party': customer.id},
                            {'method': 'credit', 'amount': Decimal('25'),
                                'party': customer.id}])]})
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([Shift(shift.id)])
            invoice, = Shift(shift.id).invoices
            self.assertEqual(invoice.party, customer)
            self.assertEqual(invoice.state, 'draft')
            self.assertEqual(invoice.untaxed_amount, Decimal('55'))


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
            FuelStationInvoiceTestCase))
    return suite
