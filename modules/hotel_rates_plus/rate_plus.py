# This file is part of the Tryton hotel_rates_plus module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.modules.product import price_digits
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If

from .exceptions import RatePlusError

_ZERO = Decimal(0)
_BLOCKING = ['confirmed', 'checked_in']


def _nights(arrival, departure):
    "Number of nights of a stay (at least one, like the rates)"
    return max((departure - arrival).days, 1)


class RateRestriction(DeactivableMixin, ModelSQL, ModelView):
    "Stay Restriction"
    __name__ = 'hotel.rate.restriction'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    room_type = fields.Many2One(
        'hotel.room.type', "Room Type",
        domain=[('hotel', '=', Eval('hotel', -1))],
        help="Leave empty to apply to all the room types.")
    start_date = fields.Date(
        "Start Date",
        domain=[If(Bool(Eval('start_date')) & Bool(Eval('end_date')),
                ('start_date', '<=', Eval('end_date')), ())],
        help="First night concerned. Empty means no lower bound.")
    end_date = fields.Date(
        "End Date",
        help="Last night concerned. Empty means no upper bound.")
    min_stay = fields.Integer(
        "Minimum Stay", domain=[If(Bool(Eval('min_stay')),
                ('min_stay', '>=', 1), ())],
        help="Minimum number of nights for an arrival in the period.")
    max_stay = fields.Integer(
        "Maximum Stay", domain=[If(Bool(Eval('max_stay')),
                ('max_stay', '>=', 1), ())],
        help="Maximum number of nights for an arrival in the period.")
    closed = fields.Boolean(
        "Closed", help="No stay may include a night of the period.")
    closed_to_arrival = fields.Boolean(
        "Closed to Arrival", help="No arrival is accepted in the period.")
    closed_to_departure = fields.Boolean(
        "Closed to Departure", help="No departure is accepted in the period.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('start_date', 'DESC'))

    @classmethod
    def default_closed(cls):
        return False

    @classmethod
    def default_closed_to_arrival(cls):
        return False

    @classmethod
    def default_closed_to_departure(cls):
        return False

    def in_period(self, day):
        return ((self.start_date is None or self.start_date <= day)
            and (self.end_date is None or day <= self.end_date))

    def check(self, arrival, departure):
        """Return the message id of the violated rule for a stay between
        two dates, or None."""
        if self.closed:
            nights = [arrival + datetime.timedelta(days=i)
                for i in range(_nights(arrival, departure))]
            if any(self.in_period(d) for d in nights):
                return 'hotel_rates_plus.msg_restriction_closed'
        if self.closed_to_arrival and self.in_period(arrival):
            return 'hotel_rates_plus.msg_restriction_arrival'
        if self.closed_to_departure and self.in_period(departure):
            return 'hotel_rates_plus.msg_restriction_departure'
        if self.in_period(arrival):
            nights = _nights(arrival, departure)
            if self.min_stay and nights < self.min_stay:
                return 'hotel_rates_plus.msg_restriction_min_stay'
            if self.max_stay and nights > self.max_stay:
                return 'hotel_rates_plus.msg_restriction_max_stay'

    @classmethod
    def check_stay(cls, hotel, room_type, arrival, departure, name=''):
        "Raise RatePlusError if the stay breaks a restriction"
        restrictions = cls.search([
                ('hotel', '=', hotel.id),
                ['OR', ('room_type', '=', None),
                    ('room_type', '=', room_type.id)],
                ])
        for restriction in restrictions:
            message = restriction.check(arrival, departure)
            if message:
                raise RatePlusError(gettext(
                        message, reservation=name,
                        room_type=room_type.rec_name,
                        min_stay=restriction.min_stay,
                        max_stay=restriction.max_stay))


class MealPlan(DeactivableMixin, ModelSQL, ModelView):
    "Meal Plan"
    __name__ = 'hotel.meal.plan'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    code = fields.Char("Code", required=True)
    included = fields.Boolean(
        "Included in Room Price",
        help="The meals are already in the room price: nothing is "
        "charged, the plan is only informative.")
    product = fields.Many2One(
        'product.product', "Product",
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        states={'required': ~Eval('included', False)},
        help="The service invoiced for the meals.")
    adult_price = fields.Numeric(
        "Price per Adult", digits=price_digits,
        domain=[('adult_price', '>=', 0)],
        states={'readonly': Eval('included', False)},
        help="Price per adult and per night.")
    child_price = fields.Numeric(
        "Price per Child", digits=price_digits,
        domain=[('child_price', '>=', 0)],
        states={'readonly': Eval('included', False)},
        help="Price per child and per night.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_hotel_unique', Unique(t, t.hotel, t.code),
                'hotel_rates_plus.msg_meal_plan_code_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_included(cls):
        return False

    @classmethod
    def default_adult_price(cls):
        return _ZERO

    @classmethod
    def default_child_price(cls):
        return _ZERO


class RateSupplement(ModelSQL, ModelView):
    "Occupancy Supplement"
    __name__ = 'hotel.rate.supplement'

    room_type = fields.Many2One(
        'hotel.room.type', "Room Type", required=True, ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        help="The service invoiced for the extra guests.")
    included_adults = fields.Integer(
        "Included Guests",
        domain=[If(Bool(Eval('included_adults')),
                ('included_adults', '>=', 1), ())],
        help="Number of guests (adults first, then children) included in "
        "the room price. Empty means the capacity of the room type.")
    extra_adult_price = fields.Numeric(
        "Extra Adult Price", digits=price_digits,
        domain=[('extra_adult_price', '>=', 0)], required=True,
        help="Price per extra adult and per night.")
    extra_child_price = fields.Numeric(
        "Extra Child Price", digits=price_digits,
        domain=[('extra_child_price', '>=', 0)], required=True,
        help="Price per extra child and per night.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('room_type_unique', Unique(t, t.room_type),
                'hotel_rates_plus.msg_supplement_unique'),
            ]

    @classmethod
    def default_extra_adult_price(cls):
        return _ZERO

    @classmethod
    def default_extra_child_price(cls):
        return _ZERO

    @property
    def included(self):
        return self.included_adults or self.room_type.capacity


class RateContract(DeactivableMixin, ModelSQL, ModelView):
    "Customer Rate Contract"
    __name__ = 'hotel.rate.contract'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    party = fields.Many2One(
        'party.party', "Customer", required=True,
        help="The company or agency that benefits from the contract.")
    room_type = fields.Many2One(
        'hotel.room.type', "Room Type",
        domain=[('hotel', '=', Eval('hotel', -1))],
        help="Leave empty to apply to all the room types.")
    start_date = fields.Date(
        "Start Date",
        domain=[If(Bool(Eval('start_date')) & Bool(Eval('end_date')),
                ('start_date', '<=', Eval('end_date')), ())])
    end_date = fields.Date("End Date")
    discount_rate = fields.Numeric(
        "Discount Rate", digits=(16, 4),
        domain=[If(Bool(Eval('discount_rate')),
                [('discount_rate', '>=', 0), ('discount_rate', '<=', 1)],
                [])],
        help="Discount on the room price (0.10 for 10%). Applied to the "
        "lines of the reservations that have no discount.")
    rate = fields.Many2One(
        'hotel.rate', "Fixed Rate",
        domain=[('room_type.hotel', '=', Eval('hotel', -1)),
            If(Bool(Eval('room_type')),
                ('room_type', '=', Eval('room_type', -1)), ())],
        help="Rate used instead of the public rate of the period.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('party', 'ASC'))

    @classmethod
    def validate(cls, contracts):
        super().validate(contracts)
        for contract in contracts:
            if not contract.discount_rate and not contract.rate:
                raise RatePlusError(gettext(
                        'hotel_rates_plus.msg_contract_empty',
                        contract=contract.rec_name))

    def get_rec_name(self, name):
        return "%s - %s" % (self.party.rec_name, self.hotel.rec_name)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('party.rec_name',) + tuple(clause[1:])]

    @classmethod
    def get_for(cls, hotel, party, room_type, day):
        "Return the contract valid for the customer at day, if any"
        contracts = cls.search([
                ('hotel', '=', hotel.id),
                ('party', '=', party.id),
                ['OR', ('room_type', '=', None),
                    ('room_type', '=', room_type.id)],
                ['OR', ('start_date', '=', None), ('start_date', '<=', day)],
                ['OR', ('end_date', '=', None), ('end_date', '>=', day)],
                ])
        # A contract for the room type is more specific than a general one
        contracts.sort(key=lambda c: c.room_type is None)
        return contracts[0] if contracts else None


class PricingRule(DeactivableMixin, ModelSQL, ModelView):
    "Dynamic Pricing Rule"
    __name__ = 'hotel.pricing.rule'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    priority = fields.Integer(
        "Priority", required=True,
        help="The matching rule with the highest priority applies.")
    occupancy_from = fields.Integer(
        "Occupancy From (%)", required=True,
        domain=[('occupancy_from', '>=', 0), ('occupancy_from', '<=', 100),
            ('occupancy_from', '<=', Eval('occupancy_to', 100))])
    occupancy_to = fields.Integer(
        "Occupancy To (%)", required=True,
        domain=[('occupancy_to', '>=', 0), ('occupancy_to', '<=', 100)])
    advance_days_min = fields.Integer(
        "Advance Days From",
        domain=[If(Bool(Eval('advance_days_min')),
                ('advance_days_min', '>=', 0), ())],
        help="Minimum number of days between the booking and the arrival.")
    advance_days_max = fields.Integer(
        "Advance Days To",
        domain=[If(Bool(Eval('advance_days_max')),
                ('advance_days_max', '>=', 0), ())],
        help="Maximum number of days between the booking and the arrival.")
    adjustment_percent = fields.Numeric(
        "Adjustment (%)", digits=(16, 2), required=True,
        help="Percentage added to the room price (negative to lower it).")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('priority', 'DESC'))

    @classmethod
    def default_priority(cls):
        return 10

    @classmethod
    def default_occupancy_from(cls):
        return 0

    @classmethod
    def default_occupancy_to(cls):
        return 100

    @classmethod
    def default_adjustment_percent(cls):
        return _ZERO

    @classmethod
    def occupancy(cls, hotel, start, end, exclude=None):
        """Average occupancy (percent) of the hotel over the nights of a
        stay, counting the confirmed and checked-in reservations."""
        pool = Pool()
        Room = pool.get('hotel.room')
        Line = pool.get('hotel.reservation.line')
        total = Room.search_count([('hotel', '=', hotel.id)])
        if not total:
            return _ZERO
        first = start.date()
        sold = 0
        nights = _nights(first, end.date())
        for i in range(nights):
            low = datetime.datetime.combine(
                first + datetime.timedelta(days=i), datetime.time.min)
            high = low + datetime.timedelta(days=1)
            domain = [
                ('reservation.hotel', '=', hotel.id),
                ('reservation.state', 'in', _BLOCKING),
                ('reservation.start', '<', high),
                ('reservation.end', '>', low),
                ]
            if exclude is not None:
                domain.append(('reservation', '!=', exclude.id))
            sold += Line.search_count(domain)
        return Decimal(sold) * 100 / (total * nights)

    def match(self, occupancy, lead_days):
        if not self.occupancy_from <= occupancy <= self.occupancy_to:
            return False
        if (self.advance_days_min is not None
                and lead_days < self.advance_days_min):
            return False
        if (self.advance_days_max is not None
                and lead_days > self.advance_days_max):
            return False
        return True

    @classmethod
    def adjustment(cls, hotel, start, end, lead_days, exclude=None):
        "Percentage to add to the room price (0 when no rule matches)"
        rules = cls.search([('hotel', '=', hotel.id)])
        if not rules:
            return _ZERO
        occupancy = cls.occupancy(hotel, start, end, exclude=exclude)
        for rule in rules:  # ordered by priority, highest first
            if rule.match(occupancy, lead_days):
                return rule.adjustment_percent
        return _ZERO
