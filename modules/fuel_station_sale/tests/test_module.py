# This file is part of the Tryton fuel_station_sale module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import create_company, set_company
from trytond.modules.fuel_station.tests.test_module import FuelStationTestCase
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction


class FuelStationSaleTestCase(ModuleTestCase):
    "Test fuel_station_sale module"
    module = 'fuel_station_sale'

    @with_transaction()
    def test_authorization_creates_sale(self):
        "Test a completed authorization creates a sale and its draft invoice"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Type = pool.get('account.account.type')
            Account = pool.get('account.account')
            Category = pool.get('product.category')
            Party = pool.get('party.party')
            Authorization = pool.get('fuel.authorization')
            helper = FuelStationTestCase()
            ctx = helper._auth_setup(company)
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
            customer, walkin = Party.create([
                    {'name': "Customer",
                        'account_receivable': receivable.id,
                        'addresses': [('create', [{}])]},
                    {'name': "Walk-in",
                        'account_receivable': receivable.id,
                        'addresses': [('create', [{}])]}])
            category, = Category.create([{
                        'name': "Fuel", 'accounting': True,
                        'account_revenue': revenue.id}])
            template = ctx['diesel'].template
            template.account_category = category
            template.salable = True
            template.sale_uom = template.default_uom
            template.save()
            station = ctx['station']
            station.sale_party = walkin
            station.save()

            def run(party=None, quantity=10.0, amount='25', tx='T'):
                auth = helper._request(
                    company, ctx, party=party.id if party else None)
                Authorization.approve([auth])
                with Transaction().set_user(0):
                    Authorization.middleware_transmitted(auth.number)
                    Authorization.middleware_confirmed(auth.number)
                    Authorization.middleware_result(
                        auth.number, quantity, amount, tx)
                return Authorization(auth.id)

            auth = run(customer, 10.0, '25', 'T1')
            self.assertEqual(auth.state, 'completed')
            self.assertEqual(auth.sale.party, customer)
            self.assertEqual(auth.sale.state, 'processing')
            self.assertEqual(auth.sale.total_amount, Decimal('25.00'))
            invoice, = auth.sale.invoices
            self.assertEqual(invoice.state, 'draft')
            self.assertEqual(invoice.party, customer)
            self.assertFalse(auth.sale.shipments)
            self.assertFalse(auth.sale.moves)
            # Walk-in customer of the station
            auth = run(None, 5.0, '12.5', 'T2')
            self.assertEqual(auth.sale.party, walkin)
            # Without any customer: authorization completed, no sale
            station.sale_party = None
            station.save()
            auth = run(None, 2.0, '5', 'T3')
            self.assertEqual(auth.state, 'completed')
            self.assertIsNone(auth.sale)
            self.assertIn('info', [l.event for l in auth.logs])


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
            FuelStationSaleTestCase))
    return suite
