# This file is part of the Tryton fuel_station_sale_point module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import fields
from trytond.modules.fuel_station.exceptions import ShiftError
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval

_ZERO = Decimal(0)


class Station(metaclass=PoolMeta):
    __name__ = 'fuel.station'

    sale_point = fields.Many2One(
        'sale.point', "Shop Point of Sale",
        domain=[('company', '=', Eval('company', -1))],
        help="Point of sale (module Sale Point) of the shop of the station. "
        "Its sales are shown in the shifts of the station, so the shop is "
        "not entered twice.")


class Shift(metaclass=PoolMeta):
    __name__ = 'fuel.shift'

    pos_sale_count = fields.Function(
        fields.Integer(
            "Shop Tickets",
            help="Number of tickets of the shop point of sale on the "
            "date of the shift."),
        'get_pos')
    pos_sale_total = fields.Function(
        fields.Numeric(
            "Shop Sales (POS)", digits='currency',
            help="Total of the tickets of the shop point of sale on the "
            "date of the shift. Informative: the cash of the shop is "
            "counted in the cash session of the point of sale."),
        'get_pos')

    @classmethod
    def get_pos(cls, shifts, names):
        Sale = Pool().get('sale.point.sale')
        result = {name: {} for name in names}
        for shift in shifts:
            point = shift.station.sale_point
            sales = []
            if point:
                sales = Sale.search([
                        ('point', '=', point.id),
                        ('date', '=', shift.date),
                        ('state', 'in', ['done', 'posted'])])
            values = {
                'pos_sale_count': len(sales),
                'pos_sale_total': shift.currency.round(
                    sum((s.total for s in sales), _ZERO)),
                }
            for name in names:
                result[name][shift.id] = values[name]
        return result
