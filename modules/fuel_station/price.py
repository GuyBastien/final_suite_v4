# This file is part of the Tryton fuel_station module.
import datetime

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Unique, fields
from trytond.modules.product import price_digits
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction

from .exceptions import PriceError


class Price(ModelSQL, ModelView):
    "Fuel Price"
    __name__ = 'fuel.price'

    company = fields.Many2One('company.company', "Company", required=True,
        help="Company that owns the record.")
    station = fields.Many2One(
        'fuel.station', "Station",
        domain=[('company', '=', Eval('company', -1))],
        help="Leave empty for a price valid for every station of the "
        "company; a price set for a station takes precedence.")
    product = fields.Many2One(
        'product.product', "Fuel", required=True,
        domain=[('template.fuel', '=', True)],
        context={'company': Eval('company', -1)},
        help="Product concerned.")
    date = fields.Date(
        "Effective Date", required=True,
        help="The price applies from this date until the next price.")
    price = fields.Numeric(
        "Price", digits=price_digits, required=True,
        domain=[('price', '>=', 0)],
        help="Selling price per gallon, taxes included.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('unique_price', Unique(t, t.company, t.station, t.product, t.date),
                'fuel_station.msg_price_unique'),
            ]
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('product', 'ASC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @classmethod
    def get_price(cls, station, product, date=None):
        "Price in force for the station on date (station price first)"
        date = date or Pool().get('ir.date').today()
        for station_domain in ([station.id], [None]):
            prices = cls.search([
                    ('company', '=', station.company.id),
                    ('station', 'in', station_domain),
                    ('product', '=', product.id),
                    ('date', '<=', date),
                    ], order=[('date', 'DESC')], limit=1)
            if prices:
                return prices[0].price
        raise PriceError(gettext(
                'fuel_station.msg_price_missing',
                product=product.rec_name, station=station.rec_name,
                date=date))
