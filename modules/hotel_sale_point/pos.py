# This file is part of the Tryton hotel_sale_point module.
from trytond.i18n import gettext
from trytond.model import ModelView
from trytond.modules.hotel_pms.exceptions import ReservationError
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.point.sale'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_room': {
                    'invisible': (Eval('state') != 'open')
                    | ~Eval('reservation'),
                    'depends': ['state', 'reservation'],
                    },
                })

    @classmethod
    @ModelView.button
    def pay_room(cls, sales):
        "Charge the whole amount to the room of the checked-in guest"
        Method = Pool().get('sale.point.payment.method')

        def getter(sale):
            if not sale.reservation:
                raise ReservationError(gettext(
                        'hotel_sale_point.msg_no_reservation',
                        sale=sale.rec_name))
            methods = Method.search([
                    ('company', '=', sale.company.id),
                    ('room_charge', '=', True)], limit=1)
            if not methods:
                raise ReservationError(gettext(
                        'hotel_sale_point.msg_no_room_method',
                        company=sale.company.rec_name))
            return methods[0]
        cls._quick_pay(sales, getter)
