# This file is part of the Tryton mobile_payment module.
"Reusable fixtures for the tests of mobile_payment and its providers"
from decimal import Decimal

from trytond.modules.account.tests import create_chart, get_fiscalyear
from trytond.modules.company.tests import create_company
from trytond.modules.currency.tests import create_currency
from trytond.pool import Pool


class MobileFixture:
    "Mixin: a company in gourdes with a point of sale and open tickets"

    def _company(self, currency='HTG'):
        return create_company(currency=create_currency(currency))

    def _pos(self, company):
        "Return (point, product, clearing_account)"
        pool = Pool()
        Account = pool.get('account.account')
        Category = pool.get('product.category')
        Template = pool.get('product.template')
        Uom = pool.get('product.uom')
        POS = pool.get('sale.point')
        if not Account.search([('company', '=', company.id)], limit=1):
            create_chart(company)
        fiscalyear = get_fiscalyear(company)
        fiscalyear.save()
        fiscalyear.create_period([fiscalyear])
        revenue, = Account.search([
                ('type.revenue', '=', True),
                ('company', '=', company.id)], limit=1)
        expense, = Account.search([
                ('type.expense', '=', True),
                ('company', '=', company.id)], limit=1)
        clearing, = Account.create([{
                    'name': "Mobile clearing",
                    'parent': revenue.parent.id,
                    'type': revenue.type.id, 'company': company.id}])
        category, = Category.create([{
                    'name': "Goods", 'accounting': True,
                    'account_revenue': revenue.id,
                    'account_expense': expense.id}])
        unit, = Uom.search([('name', '=', "Unit")])
        template, = Template.create([{
                    'name': "Coffee", 'type': 'service',
                    'account_category': category.id,
                    'default_uom': unit.id, 'sale_uom': unit.id,
                    'salable': True, 'list_price': Decimal('100'),
                    'products': [('create', [{}])]}])
        sequence_type_id = pool.get('ir.model.data').get_id(
            'sale_point', 'sequence_type_sale')
        sequence, = pool.get('ir.sequence.strict').create([{
                    'name': "POS", 'company': company.id,
                    'sequence_type': sequence_type_id}])
        journal, = pool.get('account.journal').search(
            [('type', '=', 'revenue')], limit=1)
        Location = pool.get('stock.location')
        storage, input_, output = Location.create([
                {'name': "Storage", 'type': 'storage'},
                {'name': "Input", 'type': 'storage'},
                {'name': "Output", 'type': 'storage'}])
        warehouse, = Location.create([{
                    'name': "WH", 'type': 'warehouse',
                    'storage_location': storage.id,
                    'input_location': input_.id,
                    'output_location': output.id}])
        customer, = Location.search([('type', '=', 'customer')], limit=1)
        point, = POS.create([{
                    'name': "Shop", 'company': company.id,
                    'sequence': sequence.id,
                    'storage_location': storage.id,
                    'customer_location': customer.id,
                    'journal': journal.id}])
        return point, template.products[0], clearing

    def _sale(self, company, point, product, quantity=1., price='100'):
        pool = Pool()
        Sale = pool.get('sale.point.sale')
        today = pool.get('ir.date').today()
        return Sale.create([{
                    'company': company.id, 'point': point.id, 'date': today,
                    'lines': [('create', [{
                                    'product': product.id,
                                    'quantity': quantity,
                                    'unit_list_price': Decimal(price),
                                    'unit_gross_price': Decimal(price),
                                    }])]}])[0]

    def _account(self, company, clearing, provider, name="Mobile", **values):
        pool = Pool()
        Method = pool.get('sale.point.payment.method')
        Account = pool.get('mobile.payment.account')
        method, = Method.create([{
                    'name': name, 'company': company.id,
                    'account': clearing.id}])
        values.setdefault('provider', provider)
        return Account.create([dict(
                    company=company.id, name=name, method=method.id,
                    **values)])[0]
