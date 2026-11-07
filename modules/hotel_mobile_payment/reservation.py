# This file is part of the Tryton hotel_mobile_payment module.
"""Pay a hotel stay with MonCash, NatCash... (mobile_payment).

The confirmed payment is recorded as a deposit of the reservation, so the
existing billing (hotel_billing) deducts it from the final invoice: nothing
of the payment dialogue or of the billing is copied here."""
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction

from trytond.modules.hotel_billing.exceptions import BillingError
from trytond.modules.mobile_payment.exceptions import MobilePaymentError

_ZERO = Decimal(0)
PAYABLE = ['confirmed', 'checked_in']


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    mobile_transactions = fields.One2Many(
        'mobile.payment.transaction', 'reservation', "Mobile Payments",
        readonly=True,
        help="MonCash, NatCash and other mobile payments of this stay.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_mobile': {
                    'invisible': ~Eval('state').in_(PAYABLE),
                    'depends': ['state']},
                })

    @classmethod
    @ModelView.button_action('mobile_payment.wizard_pay')
    def pay_mobile(cls, reservations):
        pass

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('mobile_transactions', None)
        return super().copy(reservations, default=default)


class Deposit(metaclass=PoolMeta):
    __name__ = 'hotel.deposit'

    mobile_transaction = fields.Many2One(
        'mobile.payment.transaction', "Mobile Payment", readonly=True,
        ondelete='RESTRICT',
        states={'invisible': ~Eval('mobile_transaction')},
        help="Operator confirmation that created this deposit.")

    @classmethod
    def create(cls, vlist):
        # Created by the confirmation of the operator, never typed.
        if not Transaction().context.get('mobile_confirm'):
            for values in vlist:
                if values.get('method') == 'mobile':
                    raise BillingError(gettext(
                            'hotel_mobile_payment.msg_mobile_typed'))
        return super().create(vlist)

    @classmethod
    def copy(cls, deposits, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('mobile_transaction', None)
        return super().copy(deposits, default=default)


class MobileTransaction(metaclass=PoolMeta):
    __name__ = 'mobile.payment.transaction'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'draft',
            'invisible': ~Eval('reservation')},
        help="Stay paid by this transaction.")

    @classmethod
    def target_fields(cls):
        return super().target_fields() + ['reservation']

    def _target_open(self):
        if self.reservation:
            return self.reservation.state in PAYABLE
        return super()._target_open()

    def _target_due(self):
        if self.reservation:
            return max(self.reservation.balance_due or _ZERO, _ZERO)
        return super()._target_due()

    @classmethod
    def _order_base(cls, values):
        if values.get('reservation'):
            stay = Pool().get('hotel.reservation')(values['reservation'])
            return (stay.number or 'H%s' % stay.id,
                [('reservation', '=', stay.id)])
        return super()._order_base(values)

    @classmethod
    def validate(cls, transactions):
        super().validate(transactions)
        for tx in transactions:
            if tx.reservation and tx.reservation.company != tx.company:
                raise MobilePaymentError(gettext(
                        'hotel_mobile_payment.msg_company',
                        transaction=tx.rec_name))

    def _book(self):
        if not self.reservation:
            return super()._book()
        Deposit = Pool().get('hotel.deposit')
        with Transaction().set_context(mobile_confirm=True):
            Deposit.create([{
                        'reservation': self.reservation.id,
                        'amount': self.amount,
                        'method': 'mobile',
                        'reference': self.provider_ref or self.order_ref,
                        'mobile_transaction': self.id}])
        self.__class__.succeed([self])
        self.__class__(self.id).notify_result()


class Pay(metaclass=PoolMeta):
    __name__ = 'mobile.payment.pay'

    @classmethod
    def _target_start(cls, model, id):
        if model != 'hotel.reservation':
            return super()._target_start(model, id)
        stay = Pool().get('hotel.reservation')(id)
        if stay.state not in PAYABLE:
            raise MobilePaymentError(gettext(
                    'hotel_mobile_payment.msg_not_payable',
                    reservation=stay.rec_name))
        due = stay.balance_due or _ZERO
        if due <= 0:
            raise MobilePaymentError(gettext(
                    'hotel_mobile_payment.msg_nothing_due',
                    reservation=stay.rec_name))
        return {
            'document': stay.rec_name,
            'company': stay.company.id,
            'currency': stay.company.currency.id,
            'amount': due,
            }

    @classmethod
    def _target_values(cls, model, id):
        if model != 'hotel.reservation':
            return super()._target_values(model, id)
        return {'reservation': id}
