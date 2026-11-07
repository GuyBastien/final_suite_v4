=============================
POS Cash Control Scenario
=============================

Imports::

    >>> from decimal import Decimal

    >>> from proteus import Model, Wizard
    >>> from trytond.tests.tools import activate_modules
    >>> from trytond.modules.company.tests.tools import (
    ...     create_company, get_company)
    >>> from trytond.modules.account.tests.tools import (
    ...     create_fiscalyear, create_chart, get_accounts)
    >>> from trytond.modules.account_invoice.tests.tools import (
    ...     set_fiscalyear_invoice_sequences)

Activate modules::

    >>> config = activate_modules('sale_point_cash_control')

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
    >>> Session = Model.get('sale.point.cash.session')
    >>> Currency = Model.get('currency.currency')
    >>> Denomination = Model.get('sale.point.denomination')

Create company, fiscal year and chart::

    >>> _ = create_company()
    >>> company = get_company()
    >>> fiscalyear = set_fiscalyear_invoice_sequences(
    ...     create_fiscalyear(company))
    >>> fiscalyear.click('create_period')
    >>> _ = create_chart(company)
    >>> accounts = get_accounts(company)

Create product::

    >>> account_category = ProductCategory(name="Account Category")
    >>> account_category.accounting = True
    >>> account_category.account_revenue = accounts['revenue']
    >>> account_category.save()
    >>> unit, = ProductUom.find([('name', '=', 'Unit')])
    >>> template = ProductTemplate()
    >>> template.name = 'product'
    >>> template.default_uom = unit
    >>> template.salable = True
    >>> template.account_category = account_category
    >>> template.gross_price = Decimal('20.0000')
    >>> template.save()
    >>> product, = template.products

Create the denominations of the company currency::

    >>> start = Wizard('sale.point.denomination.create')
    >>> start.form.currency = company.currency
    >>> company.currency.code
    'USD'
    >>> start.execute('create_')
    >>> bill100, = Denomination.find([('value', '=', 100), ('kind', '=', 'bill')])
    >>> bill5, = Denomination.find([('value', '=', 5), ('kind', '=', 'bill')])
    >>> coin25, = Denomination.find([('value', '=', '0.25'), ('kind', '=', 'coin')])
    >>> len(Denomination.find([]))
    13

Create the point of sale with cash control::

    >>> journal_revenue, = Journal.find([('type', '=', 'revenue')], limit=1)
    >>> storage_loc, = Location.find([('code', '=', 'STO')])
    >>> customer_loc, = Location.find([('code', '=', 'CUS')])
    >>> pos = POS(name="POS")
    >>> pos.journal = journal_revenue
    >>> pos.sequence = SequenceStrict(name="POS", company=pos.company)
    >>> pos.sequence.sequence_type, = SequenceType.find(
    ...     [('name', '=', "POS")], limit=1)
    >>> pos.sequence.save()
    >>> pos.storage_location = storage_loc
    >>> pos.customer_location = customer_loc
    >>> pos.cash_count = True
    >>> pos.save()

    >>> cash_method = PaymentMethod(name="Cash")
    >>> cash_method.account = accounts['cash']
    >>> cash_method.cash = True
    >>> cash_method.save()

A sale is refused until a session is opened and counted::

    >>> sale = Sale(point=pos)
    >>> line = sale.lines.new()
    >>> line.product = product
    >>> line.quantity = 1
    >>> sale.save()
    >>> payment = sale.click('pay')
    >>> payment.form.method = cash_method
    >>> payment.execute('pay')  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    CashControlError: ...

Open a session: the lines of the opening count are created::

    >>> session = Session(point=pos)
    >>> session.save()
    >>> len(session.opening_counts)
    13
    >>> session.counted_start
    Decimal('0.00')

The opening drawer is empty (start amount is 0), so selling is allowed once the
count exists::

    >>> payment = sale.click('pay')
    >>> payment.form.method = cash_method
    >>> payment.form.amount
    Decimal('20.00')
    >>> payment.execute('pay')
    >>> sale.state
    'done'

Closing needs a count::

    >>> session.reload()
    >>> session.balance
    Decimal('20.00')
    >>> session.expected_end
    Decimal('20.00')
    >>> session.end_amount = Decimal('20.00')
    >>> session.click('close')  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    CashControlError: ...

    >>> session.click('fill_closing')
    >>> counts = {c.denomination.id: c for c in session.closing_counts}
    >>> counts[bill5.id].quantity = 3
    >>> counts[coin25.id].quantity = 20
    >>> counts[bill5.id].save()
    >>> counts[coin25.id].save()
    >>> session.reload()
    >>> session.counted_end
    Decimal('20.00')
    >>> session.closing_difference
    Decimal('0.00')

A shortage is detected: one note is suspect::

    >>> counts[bill5.id].suspect_quantity = 1
    >>> counts[bill5.id].save()
    >>> session.reload()
    >>> session.counted_end
    Decimal('15.00')
    >>> session.suspect_amount
    Decimal('5.00')
    >>> session.closing_difference
    Decimal('-5.00')
    >>> session.click('close')  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    CashControlError: ...

Fix the count and use it to close::

    >>> counts[bill5.id].suspect_quantity = 0
    >>> counts[bill5.id].save()
    >>> session.click('use_count')
    >>> session.end_amount
    Decimal('20.00')
    >>> session.click('close')
    >>> session.state
    'closed'

The counts cannot be changed once closed::

    >>> counts[bill5.id].quantity = 4
    >>> counts[bill5.id].save()  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    CashControlError: ...

The next session starts with the previous end amount and requires a count::

    >>> next_session = Session(point=pos)
    >>> next_session.save()
    >>> next_session.start_amount
    Decimal('20.00')
    >>> next_session.opening_difference
    Decimal('-20.00')
