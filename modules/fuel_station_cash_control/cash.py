# This file is part of the Tryton fuel_station_cash_control module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, fields
from trytond.modules.fuel_station.exceptions import FuelWarning, ShiftError
from trytond.modules.sale_point_cash_control.cash import CountMixin
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.transaction import Transaction

_ZERO = Decimal(0)
KINDS = [('bill', "Banknote"), ('coin', "Coin")]


class Station(metaclass=PoolMeta):
    __name__ = 'fuel.station'

    cash_control = fields.Boolean(
        "Cash Count Required",
        help="The attendant must count the banknotes and coins before a "
        "shift can be closed. The denominations are those of the "
        "point of sale (Sales > Denominations).")

    @classmethod
    def default_cash_control(cls):
        return True


class Shift(metaclass=PoolMeta):
    __name__ = 'fuel.shift'

    cash_counts = fields.One2Many(
        'fuel.shift.cash.count', 'shift', "Cash Count",
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Count of banknotes and coins by denomination.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'load_denominations': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def copy(cls, shifts, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('cash_counts', None)
        return super().copy(shifts, default=default)

    def _cash_count_values(self):
        counted = sum((c.amount or _ZERO for c in self.cash_counts), _ZERO)
        suspect = sum(
            (c.suspect_amount or _ZERO for c in self.cash_counts), _ZERO)
        return counted, suspect, bool(self.cash_counts)

    def _check_cash_count(self):
        super()._check_cash_count()
        Warning = Pool().get('res.user.warning')
        if self.station.cash_control and not self.cash_counts:
            raise ShiftError(gettext(
                    'fuel_station_cash_control.msg_shift_cash_count_required',
                    shift=self.rec_name))
        if self.suspect_total > 0:
            key = 'fuel_shift_suspect_%s' % self.id
            if Warning.check(key):
                raise FuelWarning(key, gettext(
                        'fuel_station_cash_control.msg_shift_suspect_cash',
                        shift=self.rec_name,
                        amount=self.currency.round(self.suspect_total)))
        if (self.cash_counts and abs(self.cash_difference)
                > self.station.cash_tolerance):
            key = 'fuel_shift_cashcount_%s' % self.id
            if Warning.check(key):
                raise FuelWarning(key, gettext(
                        'fuel_station_cash_control.'
                        'msg_shift_cash_count_difference',
                        shift=self.rec_name,
                        difference=self.currency.round(
                            self.cash_difference)))

    @classmethod
    @ModelView.button
    def load_denominations(cls, shifts):
        "Create a cash count line for each active denomination"
        pool = Pool()
        Denomination = pool.get('sale.point.denomination')
        Count = pool.get('fuel.shift.cash.count')
        to_create = []
        for shift in shifts:
            known = {c.denomination.id for c in shift.cash_counts}
            denominations = Denomination.search([
                    ('company', '=', shift.company.id)])
            if not denominations:
                raise ShiftError(gettext(
                        'fuel_station_cash_control.msg_shift_no_denomination',
                        company=shift.company.rec_name))
            for denomination in denominations:
                if denomination.id not in known:
                    to_create.append({
                            'shift': shift.id,
                            'denomination': denomination.id,
                            'quantity': 0, 'suspect_quantity': 0})
        if to_create:
            Count.create(to_create)


class ShiftCashCount(CountMixin, ModelSQL, ModelView):
    "Fuel Shift Cash Count"
    __name__ = 'fuel.shift.cash.count'

    shift = fields.Many2One(
        'fuel.shift', "Shift", required=True, ondelete='CASCADE',
        states={'readonly': Bool(Eval('id', -1) >= 0)},
        help="Shift concerned.")
    denomination = fields.Many2One(
        'sale.point.denomination', "Denomination", required=True,
        ondelete='RESTRICT',
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Bool(Eval('id', -1) >= 0)},
        help="Banknote or coin value counted.")
    company = fields.Function(
        fields.Many2One('company.company', "Company",
        help="Company of the shift."),
        'on_change_with_company', searcher='search_company')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += cls.count_constraints(
            t, 'shift', moment=False)
        cls._order.insert(0, ('shift', 'ASC'))

    @fields.depends('shift', '_parent_shift.company')
    def on_change_with_company(self, name=None):
        if self.shift and self.shift.company:
            return self.shift.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('shift.company',) + tuple(clause[1:])]

    @classmethod
    def validate(cls, counts):
        super().validate(counts)
        for count in counts:
            if count.suspect_quantity > count.quantity:
                raise ShiftError(gettext(
                        'fuel_station_cash_control.msg_cash_count_suspect'))
            if count.denomination.company != count.shift.company:
                raise ShiftError(gettext(
                        'fuel_station_cash_control.msg_cash_count_company',
                        denomination=count.denomination.rec_name))

    @classmethod
    def create(cls, vlist):
        cls._check_open(vlist=vlist)
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for counts, _ in zip(actions, actions):
            cls._check_open(counts=counts)
        super().write(*args)

    @classmethod
    def delete(cls, counts):
        cls._check_open(counts=counts)
        super().delete(counts)

    @classmethod
    def _check_open(cls, counts=(), vlist=()):
        Shift = Pool().get('fuel.shift')
        shifts = [c.shift for c in counts]
        shifts += [Shift(v['shift']) for v in vlist if v.get('shift')]
        for shift in shifts:
            if shift.state in {'closed', 'cancelled'}:
                raise ShiftError(gettext(
                        'fuel_station.msg_payment_shift_closed',
                        shift=shift.rec_name))
