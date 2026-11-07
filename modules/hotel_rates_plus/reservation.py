# This file is part of the Tryton hotel_rates_plus module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.modules.product import round_price
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval

from .exceptions import RatePlusError
from .rate_plus import _nights

_ZERO = Decimal(0)
_CLOSED = ['checked_out', 'cancelled']
_MEAL_KINDS = ['meal_adult', 'meal_child']
_SUPPLEMENT_KINDS = ['supplement_adult', 'supplement_child']


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    dynamic_pricing = fields.Boolean(
        "Dynamic Pricing",
        help="Adjust the room prices computed for a reservation with the "
        "pricing rules (occupancy of the hotel and booking lead time).")
    pricing_rules = fields.One2Many(
        'hotel.pricing.rule', 'hotel', "Pricing Rules",
        states={'invisible': ~Eval('dynamic_pricing', False)})

    @classmethod
    def default_dynamic_pricing(cls):
        return False


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    meal_plan = fields.Many2One(
        'hotel.meal.plan', "Meal Plan",
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': Eval('state').in_(_CLOSED)},
        help="Use the button to add the meals to the charges.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        for name in ['add_meal_plan', 'add_supplements']:
            cls._buttons[name] = {
                'invisible': Eval('state').in_(_CLOSED),
                'depends': ['state'],
                }

    def check_confirmable(self):
        super().check_confirmable()
        Restriction = Pool().get('hotel.rate.restriction')
        arrival, departure = self.start.date(), self.end.date()
        for line in self.lines:
            Restriction.check_stay(
                self.hotel, line.room_type, arrival, departure,
                name=self.rec_name)

    def _marker_charges(self, kinds):
        return [c for c in self.charges if c.rates_plus_kind in kinds]

    def _new_charge(self, product, description, quantity, price, kind):
        Charge = Pool().get('hotel.reservation.charge')
        charge = Charge()
        charge.reservation = self
        charge.product = product
        charge.description = description
        charge.quantity = quantity
        charge.unit = product.sale_uom
        charge.unit_price = round_price(price)
        charge.rates_plus_kind = kind
        return charge

    def apply_meal_plan(self):
        "Replace the meal charges by those of the meal plan"
        Charge = Pool().get('hotel.reservation.charge')
        old = self._marker_charges(_MEAL_KINDS)
        if old:
            Charge.delete(old)
        plan = self.meal_plan
        if plan is None or plan.included:
            return []
        if plan.product is None:
            raise RatePlusError(gettext(
                    'hotel_rates_plus.msg_meal_plan_no_product',
                    plan=plan.rec_name))
        nights = _nights(self.start.date(), self.end.date())
        charges = []
        for count, price, kind, label in [
                (self.adults, plan.adult_price, 'meal_adult',
                    gettext('hotel_rates_plus.msg_label_adults')),
                (self.children, plan.child_price, 'meal_child',
                    gettext('hotel_rates_plus.msg_label_children'))]:
            if count and price:
                charges.append(self._new_charge(
                        plan.product, "%s - %s" % (plan.rec_name, label),
                        float(count * nights), price, kind))
        Charge.save(charges)
        return charges

    def apply_supplements(self):
        "Replace the supplement charges by those of the extra guests"
        pool = Pool()
        Charge = pool.get('hotel.reservation.charge')
        Supplement = pool.get('hotel.rate.supplement')
        old = self._marker_charges(_SUPPLEMENT_KINDS)
        if old:
            Charge.delete(old)
        supplements = []
        for line in self.lines:
            found = Supplement.search([('room_type', '=', line.room_type.id)])
            if found:
                supplements.append(found[0])
        if not supplements:
            raise RatePlusError(gettext(
                    'hotel_rates_plus.msg_supplement_missing',
                    reservation=self.rec_name))
        # The guests fill the places included in each room, adults first
        included = 0
        for line in self.lines:
            supplement = self._supplement_of(line, supplements)
            included += (supplement.included if supplement
                else line.room_type.capacity)
        persons = self.adults + self.children
        extra_adults = max(self.adults - included, 0)
        extra_children = max(persons - included, 0) - extra_adults
        supplement = supplements[0]
        nights = _nights(self.start.date(), self.end.date())
        charges = []
        for count, price, kind, label in [
                (extra_adults, supplement.extra_adult_price,
                    'supplement_adult',
                    gettext('hotel_rates_plus.msg_label_extra_adults')),
                (extra_children, supplement.extra_child_price,
                    'supplement_child',
                    gettext('hotel_rates_plus.msg_label_extra_children'))]:
            if count and price:
                charges.append(self._new_charge(
                        supplement.product, label, float(count * nights),
                        price, kind))
        Charge.save(charges)
        return charges

    @staticmethod
    def _supplement_of(line, supplements):
        for supplement in supplements:
            if supplement.room_type == line.room_type:
                return supplement

    @classmethod
    @ModelView.button
    def add_meal_plan(cls, reservations):
        for reservation in reservations:
            reservation.apply_meal_plan()

    @classmethod
    @ModelView.button
    def add_supplements(cls, reservations):
        for reservation in reservations:
            reservation.apply_supplements()


class ReservationLine(metaclass=PoolMeta):
    __name__ = 'hotel.reservation.line'

    def compute(self, start, end):
        pool = Pool()
        Contract = pool.get('hotel.rate.contract')
        Rule = pool.get('hotel.pricing.rule')
        Date = pool.get('ir.date')
        reservation = self.reservation
        contract = None
        if reservation and reservation.party and reservation.hotel:
            contract = Contract.get_for(
                reservation.hotel, reservation.party, self.room_type,
                start.date())
        fixed = (contract.rate if contract and contract.rate
            and contract.rate.room_type == self.room_type else None)
        if fixed:
            mode = None if self.pricing == 'auto' else self.pricing
            price = fixed.compute(start, end, mode=mode)
            self.billed_as = price['mode']
            self.quantity = price['quantity']
            self.unit_price = round_price(
                price['amount'] / price['quantity'])
        else:
            super().compute(start, end)
            hotel = reservation.hotel if reservation else None
            if hotel and hotel.dynamic_pricing and self.unit_price:
                lead = (start.date() - Date.today()).days
                percent = Rule.adjustment(
                    hotel, start, end, lead, exclude=reservation)
                if percent:
                    self.unit_price = round_price(
                        self.unit_price * (1 + percent / 100))
        if (contract and contract.discount_rate
                and not self.discount_rate):
            self.discount_rate = contract.discount_rate


class ReservationCharge(metaclass=PoolMeta):
    __name__ = 'hotel.reservation.charge'

    rates_plus_kind = fields.Selection([
            (None, ""),
            ('meal_adult', "Meal (Adult)"),
            ('meal_child', "Meal (Child)"),
            ('supplement_adult', "Supplement (Adult)"),
            ('supplement_child', "Supplement (Child)"),
            ], "Generated By", readonly=True,
        help="Set on the charges created from a meal plan or a supplement; "
        "they are replaced when the buttons are used again.")

    @classmethod
    def copy(cls, charges, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('rates_plus_kind', None)
        return super().copy(charges, default=default)
