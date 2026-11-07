=====================
Quick Sale Scenario
=====================

Imports::

    >>> from decimal import Decimal

    >>> from proteus import Model
    >>> from trytond.tests.tools import activate_modules
    >>> from trytond.modules.company.tests.tools import (
    ...     create_company, get_company)
    >>> from trytond.modules.account.tests.tools import (
    ...     create_fiscalyear, create_chart, get_accounts)
    >>> from trytond.modules.account_invoice.tests.tools import (
    ...     set_fiscalyear_invoice_sequences)

Activate modules::

    >>> config = activate_modules('sale_point_quick')

    >>> Journal = Model.get('account.journal')
    >>> Location = Model.get('stock.location')
    >>> POS = Model.get('sale.point')
    >>> PaymentMethod = Model.get('sale.point.payment.method')
    >>> ProductCategory = Model.get('product.category')
    >>> ProductTemplate = Model.get('product.template')
    >>> ProductUom = Model.get('product.uom')
    >>> Sale = Model.get('sale.point.sale')
    >>> SequenceStrict = Model.get('ir.sequence.strict')
    >>> SequenceType = Model.get('ir.sequence.type')

    >>> _ = create_company()
    >>> company = get_company()
    >>> fiscalyear = set_fiscalyear_invoice_sequences(
    ...     create_fiscalyear(company))
    >>> fiscalyear.click('create_period')
    >>> _ = create_chart(company)
    >>> accounts = get_accounts(company)

    >>> account_category = ProductCategory(name="Account Category")
    >>> account_category.accounting = True
    >>> account_category.account_revenue = accounts['revenue']
    >>> account_category.save()
    >>> unit, = ProductUom.find([('name', '=', 'Unit')])
    >>> template = ProductTemplate()
    >>> template.name = 'Cola'
    >>> template.default_uom = unit
    >>> template.salable = True
    >>> template.pos_quick = True
    >>> template.account_category = account_category
    >>> template.gross_price = Decimal('2.0000')
    >>> template.save()
    >>> product, = template.products
    >>> product.suffix_code = 'COLA'
    >>> product.save()

    >>> journal_revenue, = Journal.find([('type', '=', 'revenue')], limit=1)
    >>> storage_loc, = Location.find([('code', '=', 'STO')])
    >>> customer_loc, = Location.find([('code', '=', 'CUS')])
    >>> cash_method = PaymentMethod(name="Cash")
    >>> cash_method.account = accounts['cash']
    >>> cash_method.cash = True
    >>> cash_method.save()
    >>> card_method = PaymentMethod(name="Card")
    >>> card_method.account = accounts['cash']
    >>> card_method.save()

    >>> pos = POS(name="POS")
    >>> pos.journal = journal_revenue
    >>> pos.sequence = SequenceStrict(name="POS", company=pos.company)
    >>> pos.sequence.sequence_type, = SequenceType.find(
    ...     [('name', '=', "POS")], limit=1)
    >>> pos.sequence.save()
    >>> pos.storage_location = storage_loc
    >>> pos.customer_location = customer_loc
    >>> pos.card_method = card_method
    >>> pos.save()

Scan a code with a quantity, then pick a favourite product::

    >>> sale = Sale(point=pos)
    >>> sale.quick_code = '3*COLA'
    >>> len(sale.lines)
    1
    >>> sale.lines[0].quantity
    3.0
    >>> sale.quick_code is None
    True
    >>> sale.quick_product = product
    >>> len(sale.lines)
    2
    >>> sale.save()
    >>> sale.total
    Decimal('8.00')

Pay in cash in one click::

    >>> sale.click('pay_cash')
    >>> sale.state
    'done'
    >>> payment, = sale.payments
    >>> payment.amount
    Decimal('8.00')
    >>> payment.method == cash_method
    True

Pay by card in one click::

    >>> sale = Sale(point=pos)
    >>> sale.quick_code = 'COLA'
    >>> sale.save()
    >>> sale.click('pay_card')
    >>> sale.state
    'done'
    >>> sale.payments[0].method == card_method
    True

An unknown code is refused::

    >>> sale = Sale(point=pos)
    >>> sale.quick_code = 'NOPE'  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    PosError: ...
