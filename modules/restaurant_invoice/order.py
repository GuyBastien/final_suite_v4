# This file is part of the Tryton restaurant_invoice module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.modules.account_product.exceptions import AccountError
from trytond.modules.restaurant.exceptions import OrderError
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.transaction import Transaction

_ZERO = Decimal(0)


# Payment methods whose amount is billed on another document (the stay of
# the hotel guest, see hotel_restaurant), so no restaurant invoice.
BILLED_ELSEWHERE = {'room'}


class Restaurant(metaclass=PoolMeta):
    __name__ = 'restaurant.restaurant'

    invoice_mode = fields.Selection([
            ('never', "Never"),
            ('account', "On Account Payments"),
            ('customer', "Every Order With a Customer"),
            ], "Invoicing", required=True,
        help="When a draft customer invoice is created for a paid order.")

    @classmethod
    def default_invoice_mode(cls):
        return 'account'


class Order(metaclass=PoolMeta):
    __name__ = 'restaurant.order'

    invoices = fields.Function(
        fields.One2Many('account.invoice', None, "Invoices",
        help="Invoices generated from this record."),
        'get_invoices')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'invoice': {
                    'invisible': (Eval('state') != 'paid')
                    | ~Eval('party') | Bool(Eval('invoices')),
                    'depends': ['state', 'party', 'invoices']},
                })

    def get_invoices(self, name):
        Invoice = Pool().get('account.invoice')
        return [i.id for i in Invoice.search([
                    ('lines.origin', '=', str(self))])]

    @classmethod
    @ModelView.button
    @Workflow.transition('paid')
    def pay(cls, orders):
        super().pay(orders)
        to_invoice = []
        for order in orders:
            mode = order.restaurant.invoice_mode
            if not order.party or mode == 'never':
                continue
            if mode == 'customer' or any(
                    p.method == 'account' for p in order.payments):
                to_invoice.append(order)
        if to_invoice:
            cls.create_invoices(to_invoice)

    @classmethod
    @ModelView.button
    def invoice(cls, orders):
        "Create the draft invoice of a paid order"
        cls.create_invoices([o for o in orders if o.state == 'paid'])

    def _invoice_lines(self, invoice):
        Line = Pool().get('account.invoice.line')
        lines = []
        first_account = None
        for l in self.lines:
            if l.state == 'cancelled':
                continue
            with Transaction().set_context(company=self.company.id):
                product = l.product.__class__(l.product.id)
                try:
                    account = product.account_revenue_used
                except AccountError:
                    account = None
            if not account:
                raise OrderError(gettext(
                        'restaurant_invoice.msg_no_revenue_account',
                        product=l.product.rec_name, order=self.rec_name))
            first_account = first_account or account
            price = l.unit_price * (
                100 - (l.discount_percent or _ZERO)) / 100
            lines.append(Line(
                    invoice=invoice, type='line', quantity=l.quantity,
                    unit=l.product.default_uom, product=l.product,
                    account=account, origin=self,
                    unit_price=self.company.currency.round(price)
                    if price == price.to_integral() else price,
                    description=l.product.rec_name))
        if not lines:
            return lines
        extras = []
        if self.discount_amount:
            extras.append((gettext(
                        'restaurant_invoice.msg_line_discount'),
                    -self.discount_amount))
        if self.service_amount:
            extras.append((gettext(
                        'restaurant_invoice.msg_line_service'),
                    self.service_amount))
        if self.delivery_amount:
            extras.append((gettext(
                        'restaurant_invoice.msg_line_delivery'),
                    self.delivery_amount))
        for description, amount in extras:
            lines.append(Line(
                    invoice=invoice, type='line', quantity=1.0,
                    account=first_account, origin=self,
                    unit_price=amount, description=description))
        return lines

    @classmethod
    def create_invoices(cls, orders):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        invoices = []
        for order in orders:
            if order.invoices or not order.party or any(
                    p.method in BILLED_ELSEWHERE for p in order.payments):
                continue
            party = order.party
            with Transaction().set_context(company=order.company.id):
                party = party.__class__(party.id)
                receivable = party.account_receivable_used
            address = party.address_get(type='invoice')
            if not address:
                raise OrderError(gettext(
                        'restaurant_invoice.msg_no_invoice_address',
                        party=party.rec_name, order=order.rec_name))
            invoice = Invoice(
                company=order.company, type='out', party=party,
                currency=order.company.currency,
                invoice_date=order.date, description=order.number)
            invoice.invoice_address = address
            invoice.account = receivable
            invoice.payment_term = party.customer_payment_term
            invoice.lines = order._invoice_lines(invoice)
            if invoice.lines:
                invoices.append(invoice)
        if invoices:
            Invoice.save(invoices)
        return invoices
