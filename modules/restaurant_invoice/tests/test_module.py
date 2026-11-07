# This file is part of the Tryton restaurant_invoice module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import create_company, set_company
from trytond.modules.restaurant.tests.test_module import (
    create_item, open_session, setup_restaurant)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction


class RestaurantInvoiceTestCase(test_tryton.ModuleTestCase):
    "Test restaurant_invoice module"
    module = 'restaurant_invoice'

    @with_transaction()
    def test_account_payment_invoice(self):
        "Payment on account creates a draft invoice"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Type = pool.get('account.account.type')
            Account = pool.get('account.account')
            Party = pool.get('party.party')
            Order = pool.get('restaurant.order')
            Line = pool.get('restaurant.order.line')
            Payment = pool.get('restaurant.payment')
            Category = pool.get('product.category')
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
                        'name': "Duval", 'is_person': True,
                        'first_name': "Marie",
                        'account_receivable': receivable.id,
                        'addresses': [('create', [{}])]}])
            category, = Category.create([{
                        'name': "Food", 'accounting': True,
                        'account_revenue': revenue.id}])
            r, t1, _, _ = setup_restaurant(company)
            pizza = create_item("Pizza", '20')
            template = pizza.template
            template.account_category = category
            template.save()
            order, = Order.create([{
                        'restaurant': r.id, 'company': company.id,
                        'type': 'dine_in', 'table': t1.id,
                        'party': customer.id}])
            Line.create([{'order': order.id, 'product': pizza.id,
                        'quantity': 2.0, 'unit_price': Decimal('20')}])
            Order.send([Order(order.id)])
            Payment.create([{'order': order.id, 'method': 'account',
                        'amount': Decimal('44.00')}])
            Order.pay([Order(order.id)])
            invoice, = Order(order.id).invoices
            self.assertEqual(invoice.party, customer)
            self.assertEqual(invoice.state, 'draft')
            self.assertEqual(invoice.untaxed_amount, Decimal('44.00'))


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            RestaurantInvoiceTestCase))
    return suite
