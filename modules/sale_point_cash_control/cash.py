# This file is part of the Tryton sale_point_cash_control module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    Check, DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.modules.currency.fields import Monetary
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

from .exceptions import CashControlError, CashControlWarning

_ZERO = Decimal(0)
KINDS = [('bill', "Banknote"), ('coin', "Coin")]
MOMENTS = [('opening', "Opening"), ('closing', "Closing")]

# Standard denominations by ISO currency code: (banknotes, coins)
STANDARD = {
    'HTG': ([1000, 500, 250, 100, 50, 25], [10, 5, 1]),
    'USD': ([100, 50, 20, 10, 5, 2, 1],
        [1, '0.50', '0.25', '0.10', '0.05', '0.01']),
    'EUR': ([500, 200, 100, 50, 20, 10, 5],
        [2, 1, '0.50', '0.20', '0.10', '0.05', '0.02', '0.01']),
    'CAD': ([100, 50, 20, 10, 5], [2, 1, '0.25', '0.10', '0.05']),
    'DOP': ([2000, 1000, 500, 200, 100, 50], [25, 10, 5, 1]),
    }


class Denomination(DeactivableMixin, ModelSQL, ModelView):
    "Cash Denomination"
    __name__ = 'sale.point.denomination'

    company = fields.Many2One(
        'company.company', "Company", required=True,
        help="The company that counts this banknote or coin.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    kind = fields.Selection(
        KINDS, "Kind", required=True,
        help="Banknote or coin.")
    value = fields.Numeric(
        "Value", digits=(16, 2), required=True,
        domain=[('value', '>', 0)],
        help="Face value of one banknote or one coin.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('value_unique', Unique(t, t.company, t.kind, t.value),
                'sale_point_cash_control.msg_denomination_unique'),
            ]
        cls._order.insert(0, ('company', 'ASC'))
        cls._order.insert(1, ('value', 'DESC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_kind(cls):
        return 'bill'

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        return self.company.currency.id if self.company else None

    def get_rec_name(self, name):
        code = self.company.currency.code or '' if self.company else ''
        return '%s %s %s' % (
            dict(KINDS)[self.kind], '{:f}'.format(self.value.normalize()),
            code)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('value',) + tuple(clause[1:])]

    @classmethod
    def create_standard(cls, company):
        "Create the usual banknotes and coins of the currency of the company"
        banknotes, coins = STANDARD.get(
            (company.currency.code or '').upper(), ([], []))
        existing = {(d.kind, d.value) for d in cls.search([
                    ('company', '=', company.id)])}
        to_create = []
        for kind, values in (('bill', banknotes), ('coin', coins)):
            for value in values:
                value = Decimal(str(value))
                if (kind, value) not in existing:
                    to_create.append({
                            'company': company.id, 'kind': kind,
                            'value': value})
        return cls.create(to_create) if to_create else []


class CountMixin:
    """Fields and calculations shared by every count of banknotes and coins.

    The class using it only declares ``denomination`` (the domain differs),
    the link to its own session and its own locking rules. The restaurant
    cash count reuses this mixin instead of copying it."""
    __slots__ = ()

    kind = fields.Function(
        fields.Selection(KINDS, "Kind",
        help="Banknote or coin."), 'on_change_with_kind')
    value = fields.Function(
        fields.Numeric("Value", digits=(16, 2),
        help="Value of the banknote or coin."), 'on_change_with_value')
    quantity = fields.Integer(
        "Counted", required=True,
        domain=[('quantity', '>=', 0)],
        help="Number of banknotes or coins found in the drawer.")
    suspect_quantity = fields.Integer(
        "Suspect", required=True,
        domain=[('suspect_quantity', '>=', 0),
            ('suspect_quantity', '<=', Eval('quantity', 0))],
        help="Counterfeit, damaged or doubtful banknotes/coins among those "
        "counted. They are not added to the cash total.")
    amount = fields.Function(
        fields.Numeric("Amount", digits=(16, 2),
        help="Amount in the currency of the document, without the suspect "
        "banknotes and coins."), 'on_change_with_amount')
    suspect_amount = fields.Function(
        fields.Numeric("Suspect Amount", digits=(16, 2),
        help="Value of banknotes or coins that look counterfeit."),
        'on_change_with_suspect_amount')

    @classmethod
    def count_constraints(cls, table, session_name, moment=True):
        "SQL constraints common to the counts of every session type"
        columns = [getattr(table, session_name)]
        if moment:
            columns.append(table.moment)
        columns.append(table.denomination)
        return [
            ('count_unique', Unique(table, *columns),
                'sale_point_cash_control.msg_count_unique'),
            ('suspect_le_quantity',
                Check(table, table.suspect_quantity <= table.quantity),
                'sale_point_cash_control.msg_count_suspect'),
            ]

    @classmethod
    def default_quantity(cls):
        return 0

    @classmethod
    def default_suspect_quantity(cls):
        return 0

    @fields.depends('denomination')
    def on_change_with_kind(self, name=None):
        return self.denomination.kind if self.denomination else None

    @fields.depends('denomination')
    def on_change_with_value(self, name=None):
        return self.denomination.value if self.denomination else _ZERO

    @fields.depends('denomination', 'quantity', 'suspect_quantity')
    def on_change_with_amount(self, name=None):
        if not self.denomination:
            return _ZERO
        good = max((self.quantity or 0) - (self.suspect_quantity or 0), 0)
        return self.denomination.value * good

    @fields.depends('denomination', 'suspect_quantity')
    def on_change_with_suspect_amount(self, name=None):
        if not self.denomination:
            return _ZERO
        return self.denomination.value * (self.suspect_quantity or 0)


class CashCount(CountMixin, ModelSQL, ModelView):
    "Cash Count Line"
    __name__ = 'sale.point.cash.count'

    session = fields.Many2One(
        'sale.point.cash.session', "Session", required=True,
        ondelete='CASCADE',
        help="The cash session that is counted.")
    moment = fields.Selection(
        MOMENTS, "Moment", required=True, readonly=True,
        help="Count made when the session opens or when it closes.")
    denomination = fields.Many2One(
        'sale.point.denomination', "Denomination", required=True,
        ondelete='RESTRICT',
        help="The banknote or the coin that is counted.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += cls.count_constraints(t, 'session')
        cls._order.insert(0, ('session', 'ASC'))
        cls._order.insert(1, ('moment', 'ASC'))

    @classmethod
    def default_moment(cls):
        return Transaction().context.get('count_moment') or 'opening'

    @classmethod
    def check_open(cls, counts):
        for count in counts:
            if count.session.state != 'open':
                raise CashControlError(gettext(
                        'sale_point_cash_control.msg_count_session_closed',
                        session=count.session.rec_name))

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for counts, values in zip(actions, actions):
            cls.check_open(counts)
        super().write(*args)

    @classmethod
    def delete(cls, counts):
        cls.check_open(counts)
        super().delete(counts)


class POS(metaclass=PoolMeta):
    __name__ = 'sale.point'

    cash_control = fields.Boolean(
        "Cash Control",
        help="Require the count of banknotes and coins "
        "when the cash session opens and closes.")
    difference_type = fields.Many2One(
        'sale.point.cash.transfer.type', "Difference Type",
        domain=[('company', '=', Eval('company', -1))],
        states={'invisible': ~Eval('cash_control', False)},
        help="Transfer type used to record a cash shortage or overage "
        "found at the closing count.")

    cash_tolerance = fields.Numeric(
        "Cash Tolerance", digits=(16, 2), required=True,
        domain=[('cash_tolerance', '>=', 0)],
        states={'invisible': ~Eval('cash_control', False)},
        help="Closing difference accepted without warning.")

    @classmethod
    def default_cash_control(cls):
        return True

    @classmethod
    def default_cash_tolerance(cls):
        return _ZERO


class CashSession(metaclass=PoolMeta):
    __name__ = 'sale.point.cash.session'

    opening_counts = fields.One2Many(
        'sale.point.cash.count', 'session', "Opening Count",
        filter=[('moment', '=', 'opening')],
        context={'count_moment': 'opening'},
        states={'readonly': Eval('state') != 'open'},
        help="Banknotes and coins found in the drawer when the session opens.")
    closing_counts = fields.One2Many(
        'sale.point.cash.count', 'session', "Closing Count",
        filter=[('moment', '=', 'closing')],
        context={'count_moment': 'closing'},
        states={'readonly': Eval('state') != 'open'},
        help="Banknotes and coins found in the drawer when the session "
        "closes.")
    opening_counted = fields.Function(
        Monetary("Opening Counted", currency='currency', digits='currency',
        help="Total of the opening count."),
        'get_counted')
    closing_counted = fields.Function(
        Monetary("Closing Counted", currency='currency', digits='currency',
        help="Total of the closing count."),
        'get_counted')
    closing_suspect = fields.Function(
        Monetary("Suspect at Closing", currency='currency',
            digits='currency',
            help="Value of the suspect banknotes and coins found at the "
            "closing count. They are not part of the counted total."),
        'get_counted')
    opening_difference = fields.Function(
        Monetary("Opening Difference", currency='currency',
            digits='currency',
            help="Counted amount minus the amount left by the previous "
            "session."),
        'get_differences')
    expected_amount = fields.Function(
        Monetary("Expected Amount", currency='currency', digits='currency',
            help="Amount that must be in the drawer: start amount plus the "
            "cash received."),
        'get_differences')
    closing_difference = fields.Function(
        Monetary("Closing Difference", currency='currency',
            digits='currency',
            help="Counted amount minus expected amount. Negative: shortage; "
            "positive: overage."),
        'get_differences')
    cash_control = fields.Function(
        fields.Boolean("Cash Control",
        help="Require a count of banknotes and coins at opening and closing."), 'on_change_with_cash_control')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'use_count': {
                    'invisible': (Eval('state') != 'open')
                    | ~Eval('cash_control', False),
                    'depends': ['state', 'cash_control'],
                    },
                'record_difference': {
                    'invisible': (Eval('state') != 'open')
                    | ~Eval('cash_control', False)
                    | (Eval('closing_difference', 0) == 0),
                    'depends': ['state', 'cash_control',
                        'closing_difference'],
                    },
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('closing_difference', 0) != 0, 'danger',
                    If(Eval('opening_difference', 0) != 0, 'warning', ''))),
            ]

    @fields.depends('point')
    def on_change_with_cash_control(self, name=None):
        return bool(self.point and self.point.cash_control)

    @classmethod
    def get_counted(cls, sessions, names):
        result = {n: {} for n in names}
        for session in sessions:
            for name in names:
                moment = name.split('_')[0]
                lines = (session.opening_counts if moment == 'opening'
                    else session.closing_counts)
                attr = 'suspect_amount' if name.endswith('suspect') \
                    else 'amount'
                total = sum((getattr(l, attr) for l in lines), _ZERO)
                result[name][session.id] = session.currency.round(total)
        return result

    @classmethod
    def get_differences(cls, sessions, names):
        result = {n: {} for n in names}
        for session in sessions:
            expected = session.start_amount + session.balance
            # Counting nothing while the drawer should hold money is a gap
            # (the whole start amount is missing), not "no difference".
            opening = (session.opening_counted - session.start_amount
                if session.opening_counts else _ZERO)
            closing = (session.closing_counted - expected
                if session.closing_counts and session.closing_counted
                else _ZERO)
            values = {
                'expected_amount': expected,
                'opening_difference': opening,
                'closing_difference': closing}
            for name in names:
                result[name][session.id] = values[name]
        return result

    @classmethod
    def _prefill(cls, sessions):
        "Create one count line per denomination, copying the previous close"
        pool = Pool()
        Denomination = pool.get('sale.point.denomination')
        Count = pool.get('sale.point.cash.count')
        to_create = []
        for session in sessions:
            if not session.point or not session.point.cash_control:
                continue
            denominations = Denomination.search([
                    ('company', '=', session.point.company.id)])
            previous = {}
            if session.previous_session:
                previous = {c.denomination.id: c.quantity
                    for c in session.previous_session.closing_counts}
            for d in denominations:
                to_create.append({
                        'session': session.id, 'moment': 'opening',
                        'denomination': d.id,
                        'quantity': previous.get(d.id, 0)})
                to_create.append({
                        'session': session.id, 'moment': 'closing',
                        'denomination': d.id, 'quantity': 0})
        if to_create:
            Count.create(to_create)

    @classmethod
    def create(cls, vlist):
        sessions = super().create(vlist)
        cls._prefill(sessions)
        return sessions

    @classmethod
    @ModelView.button
    def use_count(cls, sessions):
        "Use the closing count as the end amount of the session"
        for session in sessions:
            if not session.closing_counts:
                raise CashControlError(gettext(
                        'sale_point_cash_control.msg_count_missing',
                        session=session.rec_name))
        for session in sessions:
            cls.write([session], {'end_amount': session.closing_counted})

    @classmethod
    def _book_difference(cls, session):
        "Record the closing shortage or overage as a cash transfer"
        pool = Pool()
        Transfer = pool.get('sale.point.cash.transfer')
        Date = pool.get('ir.date')
        point = session.point
        difference = session.closing_difference
        if not difference:
            return
        if not point.difference_type:
            raise CashControlError(gettext(
                    'sale_point_cash_control.msg_difference_type',
                    point=point.rec_name))
        Transfer.create([{
                    'point': point.id, 'session': session.id,
                    'type': point.difference_type.id,
                    'date': Date.today(), 'amount': difference}])

    @classmethod
    @ModelView.button
    def record_difference(cls, sessions):
        "Record the shortage or overage as a cash transfer"
        for session in sessions:
            cls._book_difference(session)

    @classmethod
    @ModelView.button
    def close(cls, sessions):
        pool = Pool()
        Warning = pool.get('res.user.warning')
        for session in sessions:
            if not session.point.cash_control:
                continue
            expected = session.start_amount + session.balance
            if (not session.closing_counts
                    or (not session.closing_counted and expected)):
                raise CashControlError(gettext(
                        'sale_point_cash_control.msg_count_missing',
                        session=session.rec_name))
            if session.opening_difference:
                key = 'cash_opening_difference_%s' % session.id
                if Warning.check(key):
                    raise CashControlWarning(key, gettext(
                            'sale_point_cash_control.msg_opening_difference',
                            session=session.rec_name,
                            difference=session.opening_difference))
            if abs(session.closing_difference) > session.point.cash_tolerance:
                key = 'cash_closing_difference_%s' % session.id
                if Warning.check(key):
                    raise CashControlWarning(key, gettext(
                            'sale_point_cash_control.msg_closing_difference',
                            session=session.rec_name,
                            difference=session.closing_difference))
            # The base module only closes when end - start == balance:
            # book any remaining difference (also under the tolerance).
            cls._book_difference(session)
            cls.write([session], {'end_amount': session.closing_counted})
        # Fresh instances: the balance cached on the old ones ignores the
        # difference transfer that was just booked.
        super().close(cls.browse([s.id for s in sessions]))
