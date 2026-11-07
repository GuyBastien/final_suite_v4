# This file is part of the Tryton restaurant module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    Check, ModelSQL, ModelView, Unique, Workflow, fields)
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from trytond.modules.sale_point_cash_control.cash import CountMixin

from .exceptions import CashError, RestaurantWarning

_ZERO = Decimal(0)

KINDS = [('bill', "Banknote"), ('coin', "Coin")]
MOMENTS = [('opening', "Opening"), ('closing', "Closing")]


class CashSession(Workflow, ModelSQL, ModelView):
    "Restaurant Cash Session"
    __name__ = 'restaurant.cash.session'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="Company that owns the record.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'draft'},
        help="Restaurant concerned.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    cashier = fields.Many2One(
        'res.user', "Cashier", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="User who runs the cash session.")
    date = fields.Date("Date", required=True, readonly=True,
        help="Date of the document.")
    opened_at = fields.DateTime("Opened at", readonly=True,
        help="Date and time of the opening.")
    closed_at = fields.DateTime("Closed at", readonly=True,
        help="Date and time of the closing.")
    state = fields.Selection([
            ('draft', "Draft"),
            ('open', "Open"),
            ('closed', "Closed"),
            ], "State", readonly=True, required=True, sort=False,
        help="Current step of the workflow.")
    counts = fields.One2Many(
        'restaurant.cash.count', 'session', "Cash Count",
        states={'readonly': Eval('state') == 'closed'},
        help="Count of banknotes and coins by denomination.")
    opening_counts = fields.One2Many(
        'restaurant.cash.count', 'session', "Opening Count",
        domain=[('moment', '=', 'opening')],
        filter=[('moment', '=', 'opening')],
        context={'default_moment': 'opening'},
        states={'readonly': Eval('state') != 'draft'},
        help="Banknotes and coins counted when opening.")
    closing_counts = fields.One2Many(
        'restaurant.cash.count', 'session', "Closing Count",
        domain=[('moment', '=', 'closing')],
        filter=[('moment', '=', 'closing')],
        context={'default_moment': 'closing'},
        states={'readonly': Eval('state') != 'open'},
        help="Banknotes and coins counted when closing.")
    payments = fields.One2Many(
        'restaurant.payment', 'session', "Payments", readonly=True,
        help="Payments received for this record.")
    opening_amount = fields.Function(
        fields.Numeric("Opening Cash", digits='currency',
        help="Total cash counted when the session is opened."), 'get_amounts')
    cash_sales = fields.Function(
        fields.Numeric("Cash Sales", digits='currency',
        help="Sales collected in cash."), 'get_amounts')
    expected_amount = fields.Function(
        fields.Numeric("Expected Cash", digits='currency',
        help="Opening cash plus cash payments received."), 'get_amounts')
    closing_amount = fields.Function(
        fields.Numeric("Counted Cash", digits='currency',
        help="Total cash counted when the session is closed."), 'get_amounts')
    suspect_amount = fields.Function(
        fields.Numeric("Suspect Notes/Coins", digits='currency',
        help="Value of banknotes or coins that look counterfeit."),
        'get_amounts')
    difference = fields.Function(
        fields.Numeric("Difference", digits='currency',
        help="Difference between counted and expected amounts."), 'get_amounts')
    notes = fields.Text("Notes",
        help="Free text for internal use.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {('draft', 'open'), ('open', 'closed')}
        cls._buttons.update({
                'fill_opening': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state']},
                'fill_closing': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                'open_': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state']},
                'close': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(Eval('difference', 0) != 0,
                    'danger', '')),
            ]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_cashier(cls):
        return Transaction().user

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'draft'

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id('restaurant', 'sequence_cash'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def delete(cls, sessions):
        for session in sessions:
            if session.state != 'draft':
                raise CashError(gettext(
                        'restaurant.msg_session_delete',
                        session=session.rec_name))
        super().delete(sessions)

    @classmethod
    def validate(cls, sessions):
        super().validate(sessions)
        for session in sessions:
            if session.restaurant.company != session.company:
                raise CashError(gettext(
                        'restaurant.msg_session_company',
                        session=session.rec_name))
            if session.state == 'open':
                others = cls.search([
                        ('restaurant', '=', session.restaurant.id),
                        ('cashier', '=', session.cashier.id),
                        ('state', '=', 'open'),
                        ('id', '!=', session.id)], limit=1)
                if others:
                    raise CashError(gettext(
                            'restaurant.msg_session_already_open',
                            cashier=session.cashier.rec_name,
                            session=others[0].rec_name))

    def get_rec_name(self, name):
        return self.number or str(self.id)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('number',) + tuple(clause[1:])]

    @classmethod
    def get_amounts(cls, sessions, names):
        result = {name: {} for name in names}
        for session in sessions:
            opening = sum((c.amount for c in session.counts
                    if c.moment == 'opening'), _ZERO)
            closing = sum((c.amount for c in session.counts
                    if c.moment == 'closing'), _ZERO)
            suspect = sum((c.suspect_amount for c in session.counts
                    if c.moment == 'closing'), _ZERO)
            sales = sum((p.amount for p in session.payments
                    if p.method == 'cash'), _ZERO)
            expected = opening + sales
            values = {
                'opening_amount': opening, 'cash_sales': sales,
                'expected_amount': expected, 'closing_amount': closing,
                'suspect_amount': suspect,
                'difference': closing - expected,
                }
            for name in names:
                result[name][session.id] = values[name]
        return result

    @classmethod
    def _fill(cls, sessions, moment):
        pool = Pool()
        Count = pool.get('restaurant.cash.count')
        Denomination = pool.get('sale.point.denomination')
        to_create = []
        for session in sessions:
            existing = {c.denomination.id for c in session.counts
                if c.moment == moment}
            for d in Denomination.search([
                        ('company', '=', session.company.id)]):
                if d.id not in existing:
                    to_create.append({
                            'session': session.id, 'moment': moment,
                            'denomination': d.id, 'quantity': 0,
                            'suspect_quantity': 0})
        if to_create:
            Count.create(to_create)

    @classmethod
    @ModelView.button
    def fill_opening(cls, sessions):
        "Add a line for every banknote and coin"
        cls._fill(sessions, 'opening')

    @classmethod
    @ModelView.button
    def fill_closing(cls, sessions):
        "Add a line for every banknote and coin"
        cls._fill(sessions, 'closing')

    @classmethod
    @ModelView.button
    @Workflow.transition('open')
    def open_(cls, sessions):
        for session in sessions:
            if not [c for c in session.counts if c.moment == 'opening']:
                raise CashError(gettext(
                        'restaurant.msg_session_no_opening_count',
                        session=session.rec_name))
            last = cls.search([
                    ('restaurant', '=', session.restaurant.id),
                    ('state', '=', 'closed')], order=[('id', 'DESC')],
                limit=1)
            if last and last[0].closing_amount != session.opening_amount:
                key = 'restaurant_cash_opening_%s' % session.id
                if Pool().get('res.user.warning').check(key):
                    raise RestaurantWarning(key, gettext(
                            'restaurant.msg_session_opening_mismatch',
                            session=session.rec_name,
                            previous=last[0].closing_amount,
                            amount=session.opening_amount))
        cls.write(sessions, {'opened_at': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    @Workflow.transition('closed')
    def close(cls, sessions):
        Warning = Pool().get('res.user.warning')
        for session in sessions:
            if not [c for c in session.counts if c.moment == 'closing']:
                raise CashError(gettext(
                        'restaurant.msg_session_no_closing_count',
                        session=session.rec_name))
            tolerance = session.restaurant.cash_tolerance
            if abs(session.difference) > tolerance:
                key = 'restaurant_cash_difference_%s' % session.id
                if Warning.check(key):
                    raise RestaurantWarning(key, gettext(
                            'restaurant.msg_session_difference',
                            session=session.rec_name,
                            difference=session.difference))
            if session.suspect_amount:
                key = 'restaurant_cash_suspect_%s' % session.id
                if Warning.check(key):
                    raise RestaurantWarning(key, gettext(
                            'restaurant.msg_session_suspect',
                            session=session.rec_name,
                            amount=session.suspect_amount))
        cls.write(sessions, {'closed_at': datetime.datetime.now()})

    @classmethod
    def get_current(cls, restaurant, user=None):
        "Open session of the restaurant for the user, else any open one"
        user = user or Transaction().user
        sessions = cls.search([
                ('restaurant', '=', restaurant.id), ('state', '=', 'open')],
            order=[('id', 'ASC')])
        for session in sessions:
            if session.cashier.id == user:
                return session
        return sessions[0] if sessions else None


class CashCount(CountMixin, ModelSQL, ModelView):
    "Restaurant Cash Count"
    __name__ = 'restaurant.cash.count'

    session = fields.Many2One(
        'restaurant.cash.session', "Session", required=True,
        ondelete='CASCADE',
        help="Cash session concerned.")
    moment = fields.Selection(MOMENTS, "Moment", required=True,
        help="Opening or closing of the session.")
    denomination = fields.Many2One(
        'sale.point.denomination', "Denomination", required=True,
        ondelete='RESTRICT',
        domain=[('company', '=', Eval('session_company', -1))],
        help="Banknote or coin value counted. The denominations are "
        "shared with the points of sale (Sales > Denominations).")
    session_company = fields.Function(
        fields.Many2One('company.company', "Company",
        help="Company of the cash session."),
        'on_change_with_session_company',
        searcher='search_session_company')
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += cls.count_constraints(t, 'session')
        cls._order.insert(0, ('session', 'ASC'))
        cls._order.insert(1, ('moment', 'ASC'))

    @classmethod
    def default_moment(cls):
        return 'opening'

    @fields.depends('session', '_parent_session.company')
    def on_change_with_session_company(self, name=None):
        if self.session and self.session.company:
            return self.session.company.id

    @classmethod
    def search_session_company(cls, name, clause):
        return [('session.company',) + tuple(clause[1:])]

    @fields.depends('session', '_parent_session.company')
    def on_change_with_currency(self, name=None):
        if self.session and self.session.company:
            return self.session.company.currency.id

    @classmethod
    def validate(cls, counts):
        super().validate(counts)
        for count in counts:
            if count.suspect_quantity > count.quantity:
                raise CashError(gettext('restaurant.msg_cash_count_suspect'))
            if count.denomination.company != count.session.company:
                raise CashError(gettext(
                        'restaurant.msg_cash_count_currency',
                        denomination=count.denomination.rec_name))
            if count.session.state == 'closed':
                raise CashError(gettext(
                        'restaurant.msg_session_closed',
                        session=count.session.rec_name))
            if count.moment == 'opening' and count.session.state == 'open':
                raise CashError(gettext(
                        'restaurant.msg_session_opening_locked',
                        session=count.session.rec_name))

    @classmethod
    def delete(cls, counts):
        for count in counts:
            if count.session.state == 'closed':
                raise CashError(gettext(
                        'restaurant.msg_session_closed',
                        session=count.session.rec_name))
        super().delete(counts)
