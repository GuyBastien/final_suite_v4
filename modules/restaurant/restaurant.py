# This file is part of the Tryton restaurant module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction

from .exceptions import RestaurantError

OPEN_STATES = ['open']
_ZERO = Decimal(0)


class Restaurant(DeactivableMixin, ModelSQL, ModelView):
    "Restaurant"
    __name__ = 'restaurant.restaurant'
    _rec_name = 'name'

    name = fields.Char("Name", required=True, translate=True,
        help="Name displayed in lists and searches.")
    code = fields.Char("Code", required=True,
        help="Short unique identifier.")
    company = fields.Many2One('company.company', "Company", required=True,
        help="Company that owns the record.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    party = fields.Many2One(
        'party.party', "Contact",
        context={'company': Eval('company', -1)},
        help="Address and phone of the establishment.")
    warehouse = fields.Many2One(
        'stock.location', "Warehouse",
        domain=[('type', '=', 'warehouse')],
        help="Stock used by the recipes and the drinks. Leave empty to "
        "disable the stock moves.")
    service_rate = fields.Numeric(
        "Service Charge (%)", digits=(5, 2), required=True,
        domain=[('service_rate', '>=', 0), ('service_rate', '<=', 100)],
        help="Added to table and bar orders.")
    tax_rate = fields.Numeric(
        "Tax Rate (%)", digits=(5, 2), required=True,
        domain=[('tax_rate', '>=', 0), ('tax_rate', '<=', 100)],
        help="Tax contained in the prices (informative on the bill).")
    delivery_fee = fields.Numeric(
        "Delivery Fee", digits='currency', required=True,
        domain=[('delivery_fee', '>=', 0)],
        help="Fee charged for a delivery.")
    min_delivery_amount = fields.Numeric(
        "Minimum Delivery Amount", digits='currency', required=True,
        domain=[('min_delivery_amount', '>=', 0)],
        help="Minimum value of the items of a delivery order.")
    free_delivery_amount = fields.Numeric(
        "Free Delivery From", digits='currency', required=True,
        domain=[('free_delivery_amount', '>=', 0)],
        help="No delivery fee from this amount (0 = never free).")
    max_discount = fields.Numeric(
        "Maximum Discount (%)", digits=(5, 2), required=True,
        domain=[('max_discount', '>=', 0), ('max_discount', '<=', 100)],
        help="Discount a waiter may give; above, a manager is needed.")
    cash_control = fields.Boolean(
        "Cash Control",
        help="Require an open cash session, with a banknote and coin count "
        "at opening and closing, to take cash payments.")
    cash_tolerance = fields.Numeric(
        "Cash Tolerance", digits='currency', required=True,
        domain=[('cash_tolerance', '>=', 0)],
        states={'invisible': ~Eval('cash_control', False)},
        help="Closing difference accepted without warning.")
    enforce_menus = fields.Boolean(
        "Enforce Menus",
        help="Only items of a menu available now can be ordered, at the "
        "price of that menu if it has one.")
    menus = fields.One2Many('restaurant.menu', 'restaurant', "Menus",
        help="Menu cards of the restaurant.")
    areas = fields.One2Many('restaurant.area', 'restaurant', "Areas",
        help="Areas of the establishment.")
    tables = fields.One2Many('restaurant.table', 'restaurant', "Tables",
        help="Tables of the restaurant.")

    @classmethod
    def default_cash_control(cls):
        return True

    @classmethod
    def default_cash_tolerance(cls):
        return Decimal(0)

    @classmethod
    def default_enforce_menus(cls):
        return False

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_company_unique', Unique(t, t.company, t.code),
                'restaurant.msg_restaurant_code_unique'),
            ]
        cls._order.insert(0, ('code', 'ASC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_service_rate(cls):
        return _ZERO

    @classmethod
    def default_tax_rate(cls):
        return _ZERO

    @classmethod
    def default_delivery_fee(cls):
        return _ZERO

    @classmethod
    def default_min_delivery_amount(cls):
        return _ZERO

    @classmethod
    def default_free_delivery_amount(cls):
        return _ZERO

    @classmethod
    def default_max_discount(cls):
        return Decimal(10)

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    def get_rec_name(self, name):
        return "[%s] %s" % (self.code, self.name)

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR', ('code',) + tuple(clause[1:]),
            ('name',) + tuple(clause[1:])]


class Area(DeactivableMixin, ModelSQL, ModelView):
    "Restaurant Area"
    __name__ = 'restaurant.area'

    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        ondelete='CASCADE',
        help="Restaurant concerned.")
    name = fields.Char("Name", required=True, translate=True,
        help="Dining room, terrace, bar, private room...")
    tables = fields.One2Many('restaurant.table', 'area', "Tables",
        help="Tables of the restaurant.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_unique', Unique(t, t.restaurant, t.name),
                'restaurant.msg_area_unique'),
            ]
        cls._order.insert(0, ('restaurant', 'ASC'))
        cls._order.insert(1, ('name', 'ASC'))


class Table(DeactivableMixin, ModelSQL, ModelView):
    "Restaurant Table"
    __name__ = 'restaurant.table'

    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        ondelete='CASCADE',
        help="Restaurant concerned.")
    area = fields.Many2One(
        'restaurant.area', "Area", required=True,
        domain=[('restaurant', '=', Eval('restaurant', -1))],
        help="Area (room, terrace, bar...) of the restaurant.")
    code = fields.Char("Number", required=True,
        help="Short unique identifier.")
    seats = fields.Integer(
        "Seats", required=True, domain=[('seats', '>', 0)],
        help="Number of seats at the table.")
    status = fields.Function(
        fields.Selection([
                ('free', "Free"),
                ('occupied', "Occupied"),
                ], "Status",
        help="Current availability."),
        'get_status', searcher='search_status')
    order = fields.Function(
        fields.Many2One('restaurant.order', "Current Order",
        help="Order concerned."), 'get_status')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_unique', Unique(t, t.restaurant, t.code),
                'restaurant.msg_table_unique'),
            ]
        cls._order.insert(0, ('restaurant', 'ASC'))
        cls._order.insert(1, ('code', 'ASC'))

    @classmethod
    def view_attributes(cls):
        from trytond.pyson import If
        return super().view_attributes() + [
            ('/tree', 'visual', If(Eval('status') == 'occupied',
                    'warning', 'success')),
            ]

    @classmethod
    def default_seats(cls):
        return 4

    def get_rec_name(self, name):
        return "%s / %s" % (self.restaurant.code, self.code)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('code',) + tuple(clause[1:])]

    @classmethod
    def get_status(cls, tables, names):
        Order = Pool().get('restaurant.order')
        orders = Order.search([
                ('table', 'in', [t.id for t in tables]),
                ('state', '=', 'open')])
        by_table = {o.table.id: o for o in orders}
        result = {}
        if 'status' in names:
            result['status'] = {
                t.id: 'occupied' if t.id in by_table else 'free'
                for t in tables}
        if 'order' in names:
            result['order'] = {
                t.id: by_table[t.id].id if t.id in by_table else None
                for t in tables}
        return result

    @classmethod
    def search_status(cls, name, clause):
        Order = Pool().get('restaurant.order')
        busy = [o.table.id for o in Order.search([
                    ('state', '=', 'open'), ('table', '!=', None)])]
        _, operator, value = clause[:3]
        want_busy = (value == 'occupied') == (operator in ('=', 'in'))
        return [('id', 'in' if want_busy else 'not in', busy)]

    @classmethod
    def validate(cls, tables):
        super().validate(tables)
        for table in tables:
            if table.area.restaurant != table.restaurant:
                raise RestaurantError(gettext(
                        'restaurant.msg_table_area', table=table.rec_name))
