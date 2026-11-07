# This file is part of the Tryton hotel module.
import math
from datetime import timedelta
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import DeactivableMixin, ModelSQL, ModelView, fields
from trytond.modules.product import price_digits
from trytond.pyson import Bool, Eval, If

from .exceptions import RateError

# Nights starting on Friday and Saturday are "weekend" nights
WEEKEND_WEEKDAYS = (4, 5)


class Rate(DeactivableMixin, ModelSQL, ModelView):
    "Room Rate (per hour and per night)"
    __name__ = 'hotel.rate'

    room_type = fields.Many2One(
        'hotel.room.type', "Room Type", required=True, ondelete='CASCADE')
    name = fields.Char("Name", required=True, translate=True)
    start_date = fields.Date(
        "Start Date",
        domain=[
            If(Bool(Eval('start_date')) & Bool(Eval('end_date')),
                ('start_date', '<=', Eval('end_date')), ())],
        help="First day of validity. Empty means no lower bound.")
    end_date = fields.Date(
        "End Date",
        help="Last day of validity. Empty means no upper bound.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency"),
        'on_change_with_currency')
    night_price = fields.Numeric(
        "Night Price", digits=price_digits, required=True,
        domain=[('night_price', '>=', 0)])
    weekend_night_price = fields.Numeric(
        "Weekend Night Price", digits=price_digits,
        domain=[If(Bool(Eval('weekend_night_price')),
                ('weekend_night_price', '>=', 0), ())],
        help="Price of nights starting on Friday and Saturday. "
        "Empty means the night price applies.")
    hour_price = fields.Numeric(
        "Hour Price", digits=price_digits,
        domain=[If(Bool(Eval('hour_price')), ('hour_price', '>=', 0), ())],
        help="Price per hour. Empty disables hourly pricing.")
    min_hours = fields.Integer(
        "Minimum Hours", required=True, domain=[('min_hours', '>=', 1)],
        help="Minimum number of hours billed for an hourly stay.")
    max_hours = fields.Integer(
        "Maximum Hours", required=True, domain=[('max_hours', '>=', 1)],
        help="Longest stay still billed per hour; beyond it the nightly "
        "price applies.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('start_date', 'DESC'))

    @classmethod
    def default_min_hours(cls):
        return 1

    @classmethod
    def default_max_hours(cls):
        return 12

    @fields.depends('room_type', '_parent_room_type.hotel')
    def on_change_with_currency(self, name=None):
        if self.room_type and self.room_type.hotel:
            return self.room_type.hotel.company.currency.id

    def compute(self, start, end, mode=None):
        """Return the price of a stay between two datetimes.

        mode is 'hourly', 'nightly' or None (automatic: hourly when the stay
        fits in max_hours and an hour price is set, nightly otherwise).
        The result is a dict with mode, quantity, unit_price and amount.
        """
        if end <= start:
            raise RateError(gettext('hotel.msg_rate_invalid_period'))
        currency = self.room_type.hotel.company.currency
        hours = math.ceil((end - start).total_seconds() / 3600)
        if mode is None:
            if self.hour_price is not None and hours <= self.max_hours:
                mode = 'hourly'
            else:
                mode = 'nightly'

        if mode == 'hourly':
            if self.hour_price is None:
                raise RateError(gettext(
                        'hotel.msg_rate_no_hour_price', rate=self.rec_name))
            quantity = max(hours, self.min_hours)
            amount = self.hour_price * quantity
            # An hourly stay never costs more than a night
            if self.night_price is not None and amount > self.night_price:
                amount = self.night_price
            amount = currency.round(amount)
            return {
                'mode': 'hourly',
                'quantity': Decimal(quantity),
                'unit_price': currency.round(amount / quantity),
                'amount': amount,
                }

        first, last = start.date(), end.date()
        nights = max((last - first).days, 1)
        amount = Decimal(0)
        for i in range(nights):
            weekday = (first + timedelta(days=i)).weekday()
            if (self.weekend_night_price is not None
                    and weekday in WEEKEND_WEEKDAYS):
                amount += self.weekend_night_price
            else:
                amount += self.night_price
        amount = currency.round(amount)
        return {
            'mode': 'nightly',
            'quantity': Decimal(nights),
            'unit_price': currency.round(amount / nights),
            'amount': amount,
            }
