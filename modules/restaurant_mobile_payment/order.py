# This file is part of the Tryton restaurant_mobile_payment module.
"""Pay a restaurant order with MonCash, NatCash... (mobile_payment).

Nothing of the payment dialogue is copied here: the wizard, the operator
accounts, the verification scheduler and the notifications are those of
mobile_payment. This module only says how a restaurant order is paid."""
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction

from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.restaurant.exceptions import OrderError

_ZERO = Decimal(0)


class Order(metaclass=PoolMeta):
    __name__ = 'restaurant.order'

    mobile_transactions = fields.One2Many(
        'mobile.payment.transaction', 'restaurant_order', "Mobile Payments",
        readonly=True,
        help="MonCash, NatCash and other mobile payments of this order.")
    mobile_pending = fields.Function(
        fields.Boolean("Mobile Payment Pending"), 'get_mobile_pending')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_mobile': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                })

    @classmethod
    def get_mobile_pending(cls, orders, name):
        return {o.id: any(t.state == 'pending' for t in o.mobile_transactions)
            for o in orders}

    @classmethod
    @ModelView.button_action('mobile_payment.wizard_pay')
    def pay_mobile(cls, orders):
        pass

    @classmethod
    def copy(cls, orders, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('mobile_transactions', None)
        return super().copy(orders, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, orders):
        for order in orders:
            if any(t.state == 'pending' for t in order.mobile_transactions):
                raise MobilePaymentError(gettext(
                        'restaurant_mobile_payment.msg_cancel_pending',
                        order=order.rec_name))
        super().cancel(orders)

    @classmethod
    @ModelView.button
    @Workflow.transition('paid')
    def pay(cls, orders):
        for order in orders:
            if any(t.state == 'pending' for t in order.mobile_transactions):
                raise MobilePaymentError(gettext(
                        'restaurant_mobile_payment.msg_close_pending',
                        order=order.rec_name))
        super().pay(orders)


class Payment(metaclass=PoolMeta):
    __name__ = 'restaurant.payment'

    mobile_transaction = fields.Many2One(
        'mobile.payment.transaction', "Mobile Payment", readonly=True,
        ondelete='RESTRICT',
        states={'invisible': ~Eval('mobile_transaction')},
        help="Operator confirmation that created this payment.")

    @classmethod
    def create(cls, vlist):
        # A mobile payment is created by the confirmation of the operator,
        # never typed: otherwise anybody could record a payment that never
        # reached the account of the restaurant.
        if not Transaction().context.get('mobile_confirm'):
            for values in vlist:
                if values.get('method') == 'mobile':
                    raise OrderError(gettext(
                            'restaurant_mobile_payment.msg_mobile_typed'))
        return super().create(vlist)

    @classmethod
    def copy(cls, payments, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('mobile_transaction', None)
        return super().copy(payments, default=default)


class MobileTransaction(metaclass=PoolMeta):
    __name__ = 'mobile.payment.transaction'

    restaurant_order = fields.Many2One(
        'restaurant.order', "Restaurant Order", ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'draft',
            'invisible': ~Eval('restaurant_order')},
        help="Restaurant order paid by this transaction.")

    @classmethod
    def target_fields(cls):
        return super().target_fields() + ['restaurant_order']

    def _target_open(self):
        if self.restaurant_order:
            return self.restaurant_order.state == 'open'
        return super()._target_open()

    def _target_due(self):
        if self.restaurant_order:
            return max(self.restaurant_order.balance, _ZERO)
        return super()._target_due()

    @classmethod
    def _order_base(cls, values):
        if values.get('restaurant_order'):
            order = Pool().get('restaurant.order')(values['restaurant_order'])
            return (order.number or 'R%s' % order.id,
                [('restaurant_order', '=', order.id)])
        return super()._order_base(values)

    @classmethod
    def validate(cls, transactions):
        super().validate(transactions)
        for tx in transactions:
            if (tx.restaurant_order
                    and tx.restaurant_order.company != tx.company):
                raise MobilePaymentError(gettext(
                        'restaurant_mobile_payment.msg_company',
                        transaction=tx.rec_name))

    def _book(self):
        if not self.restaurant_order:
            return super()._book()
        pool = Pool()
        Payment = pool.get('restaurant.payment')
        with Transaction().set_context(mobile_confirm=True):
            payment, = Payment.create([{
                        'order': self.restaurant_order.id,
                        'method': 'mobile',
                        'amount': self.amount,
                        'reference': self.provider_ref or self.order_ref,
                        'mobile_transaction': self.id}])
        self.__class__.succeed([self])
        self.__class__(self.id).notify_result()


class Pay(metaclass=PoolMeta):
    __name__ = 'mobile.payment.pay'

    @classmethod
    def _target_start(cls, model, id):
        if model != 'restaurant.order':
            return super()._target_start(model, id)
        order = Pool().get('restaurant.order')(id)
        if order.state != 'open':
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_sale_not_open',
                    sale=order.rec_name))
        if order.balance <= 0:
            raise MobilePaymentError(gettext(
                    'restaurant_mobile_payment.msg_nothing_due',
                    order=order.rec_name))
        return {
            'document': order.rec_name,
            'company': order.company.id,
            'currency': order.company.currency.id,
            'amount': order.balance,
            }

    @classmethod
    def _target_values(cls, model, id):
        if model != 'restaurant.order':
            return super()._target_values(model, id)
        return {'restaurant_order': id}
