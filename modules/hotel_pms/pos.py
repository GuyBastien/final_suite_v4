# This file is part of the Tryton hotel_pms module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval, If

from .exceptions import ReservationError

_ZERO = Decimal(0)


class PaymentMethod(metaclass=PoolMeta):
    __name__ = 'sale.point.payment.method'

    room_charge = fields.Boolean(
        "Room Charge",
        domain=[If(Eval('cash', False), ('room_charge', '=', False), ())],
        help="Charge the guest of the checked-in reservation. The amount "
        "is settled with the sale of the stay at check-out.\n"
        "The account must be a revenue account used to clear the "
        "charges.")


class POSSale(metaclass=PoolMeta):
    __name__ = 'sale.point.sale'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation",
        domain=[('state', '=', 'checked_in'),
            ('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'open'},
        help="The stay to which the sale can be charged.")


class POSPayment(metaclass=PoolMeta):
    __name__ = 'sale.point.payment'

    settled = fields.Boolean("Settled", readonly=True)

    @classmethod
    def default_settled(cls):
        return False

    @classmethod
    def copy(cls, payments, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('settled', False)
        return super().copy(payments, default=default)

    @classmethod
    def validate(cls, payments):
        super().validate(payments)
        for payment in payments:
            if not payment.method.room_charge or payment.settled:
                continue
            reservation = payment.sale.reservation
            if reservation is None or reservation.state != 'checked_in':
                raise ReservationError(gettext(
                        'hotel_pms.msg_pos_room_charge_no_reservation',
                        sale=payment.sale.rec_name))


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    room_charge_product = fields.Many2One(
        'product.product', "Room Charge Product",
        domain=[('salable', '=', True), ('type', '=', 'service')],
        context={'company': Eval('company', -1)},
        help="Service sold on the stay to settle the room charges of the "
        "point of sale. Its revenue account must be the clearing account "
        "of the room charge payment method.")
