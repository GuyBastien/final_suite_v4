# This file is part of the Tryton sale_mobile_payment module.
"""Pay a customer invoice, or the invoice of a sale, with MonCash, NatCash...

A sale is paid through its invoice (the Tryton logic): the payment is booked
with Invoice.pay_invoice() on the payment method of the invoice module, so the
invoice is reconciled and the accounting is the standard one. The dialogue,
accounts, QR code, scheduler and notifications are those of mobile_payment."""
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction

from trytond.modules.mobile_payment.exceptions import MobilePaymentError

_ZERO = Decimal(0)


class Account(metaclass=PoolMeta):
    __name__ = 'mobile.payment.account'

    invoice_payment_method = fields.Many2One(
        'account.invoice.payment.method', "Invoice Payment Method",
        domain=[('company', '=', Eval('company', -1))],
        help="Payment method of the invoices (cash journal and accounts) on "
        "which the confirmed payments of this operator are booked. Needed to "
        "pay invoices and sales by mobile.")


class MobileTransaction(metaclass=PoolMeta):
    __name__ = 'mobile.payment.transaction'

    invoice = fields.Many2One(
        'account.invoice', "Invoice", ondelete='RESTRICT',
        domain=[('type', '=', 'out')],
        states={'readonly': Eval('state') != 'draft',
            'invisible': ~Eval('invoice')},
        help="Customer invoice paid by this transaction.")

    @classmethod
    def target_fields(cls):
        return super().target_fields() + ['invoice']

    def _target_open(self):
        if self.invoice:
            return (self.invoice.state == 'posted'
                and self.invoice.amount_to_pay > 0)
        return super()._target_open()

    def _target_due(self):
        if self.invoice:
            return max(self.invoice.amount_to_pay, _ZERO)
        return super()._target_due()

    @classmethod
    def _order_base(cls, values):
        if values.get('invoice'):
            invoice = Pool().get('account.invoice')(values['invoice'])
            return (invoice.number or 'F%s' % invoice.id,
                [('invoice', '=', invoice.id)])
        return super()._order_base(values)

    @classmethod
    def validate(cls, transactions):
        super().validate(transactions)
        for tx in transactions:
            if not tx.invoice:
                continue
            if tx.invoice.company != tx.company:
                raise MobilePaymentError(gettext(
                        'sale_mobile_payment.msg_company',
                        transaction=tx.rec_name))
            if tx.invoice.currency != tx.company.currency:
                raise MobilePaymentError(gettext(
                        'sale_mobile_payment.msg_currency',
                        invoice=tx.invoice.rec_name,
                        currency=tx.company.currency.code))

    @classmethod
    def request(cls, transactions):
        for tx in transactions:
            if tx.invoice and not tx.account.invoice_payment_method:
                raise MobilePaymentError(gettext(
                        'sale_mobile_payment.msg_no_invoice_method',
                        account=tx.account.rec_name))
        super().request(transactions)

    def _book(self):
        if not self.invoice:
            return super()._book()
        pool = Pool()
        Invoice = pool.get('account.invoice')
        date = pool.get('ir.date').today()
        reference = self.provider_ref or self.order_ref
        invoice = Invoice(self.invoice.id)
        lines = invoice.pay_invoice(
            self.amount, self.account.invoice_payment_method, date,
            gettext('sale_mobile_payment.msg_pay_description',
                provider=self.account.rec_name, reference=reference))
        # Same as the standard payment wizard of the invoice module: when the
        # invoice is fully paid, letter it with the payment and let it become
        # paid; a partial payment stays on the invoice, to be lettered later.
        to_reconcile, remainder = invoice.get_reconcile_lines_for_amount(
            self.amount, invoice.currency)
        if not remainder:
            to_reconcile = list(to_reconcile) + list(lines)
            if to_reconcile:
                pool.get('account.move.line').reconcile(to_reconcile)
        Invoice.process([Invoice(invoice.id)])
        self.__class__.succeed([self])
        self.__class__(self.id).notify_result()


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    mobile_transactions = fields.One2Many(
        'mobile.payment.transaction', 'invoice', "Mobile Payments",
        readonly=True,
        help="MonCash, NatCash and other mobile payments of this invoice.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_mobile': {
                    'invisible': (
                        (Eval('state') != 'posted') | (Eval('type') != 'out')
                        | (Eval('amount_to_pay', 0) <= 0)),
                    'depends': ['state', 'type', 'amount_to_pay']},
                })

    @classmethod
    @ModelView.button_action('mobile_payment.wizard_pay')
    def pay_mobile(cls, invoices):
        pass

    @classmethod
    def copy(cls, invoices, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('mobile_transactions', None)
        return super().copy(invoices, default=default)


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.sale'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_mobile': {
                    'invisible': Eval('invoice_state') != 'waiting',
                    'depends': ['invoice_state']},
                })

    @classmethod
    @ModelView.button_action('mobile_payment.wizard_pay')
    def pay_mobile(cls, sales):
        pass

    def _mobile_invoice(self):
        "The posted invoice of the sale that still has to be paid"
        due = [i for i in self.invoices
            if i.type == 'out' and i.state == 'posted' and i.amount_to_pay > 0]
        if not due:
            raise MobilePaymentError(gettext(
                    'sale_mobile_payment.msg_sale_nothing_due',
                    sale=self.rec_name))
        if len(due) > 1:
            raise MobilePaymentError(gettext(
                    'sale_mobile_payment.msg_sale_many_invoices',
                    sale=self.rec_name))
        return due[0]


class Pay(metaclass=PoolMeta):
    __name__ = 'mobile.payment.pay'

    @classmethod
    def _invoice_of(cls, model, id):
        pool = Pool()
        if model == 'sale.sale':
            return pool.get('sale.sale')(id)._mobile_invoice()
        return pool.get('account.invoice')(id)

    @classmethod
    def _target_start(cls, model, id):
        if model not in {'sale.sale', 'account.invoice'}:
            return super()._target_start(model, id)
        invoice = cls._invoice_of(model, id)
        if invoice.type != 'out' or invoice.state != 'posted':
            raise MobilePaymentError(gettext(
                    'sale_mobile_payment.msg_not_payable',
                    invoice=invoice.rec_name))
        if invoice.amount_to_pay <= 0:
            raise MobilePaymentError(gettext(
                    'sale_mobile_payment.msg_nothing_due',
                    invoice=invoice.rec_name))
        return {
            'document': invoice.rec_name,
            'company': invoice.company.id,
            'currency': invoice.company.currency.id,
            'amount': invoice.amount_to_pay,
            }

    @classmethod
    def _target_values(cls, model, id):
        if model not in {'sale.sale', 'account.invoice'}:
            return super()._target_values(model, id)
        return {'invoice': cls._invoice_of(model, id).id}
