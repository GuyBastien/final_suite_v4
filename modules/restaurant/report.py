# This file is part of the Tryton restaurant module.
from decimal import Decimal

from sql import Literal
from sql.aggregate import Max, Min, Sum
from sql.conditionals import Coalesce

from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.report import Report

from .order import ORDER_TYPES

_ZERO = Decimal(0)


class SalesReport(ModelSQL, ModelView):
    "Restaurant Sales by Day"
    __name__ = 'restaurant.report.sales'

    company = fields.Many2One('company.company', "Company", readonly=True,
        help="Company that owns the record.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", readonly=True,
        help="Restaurant concerned.")
    date = fields.Date("Date", readonly=True,
        help="Date of the document.")
    type = fields.Selection(ORDER_TYPES, "Type", readonly=True,
        help="Kind of record.")
    product = fields.Many2One(
        'product.product', "Item", readonly=True,
        context={'company': Eval('company', -1)},
        help="Product concerned.")
    quantity = fields.Float("Quantity", digits=(16, 2), readonly=True,
        help="Quantity, in the unit of measure.")
    amount = fields.Numeric("Sales", digits=(16, 2), readonly=True,
        help="Amount in the currency of the document.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('restaurant', 'ASC'))

    @classmethod
    def table_query(cls):
        pool = Pool()
        Line = pool.get('restaurant.order.line')
        Order = pool.get('restaurant.order')
        line = Line.__table__()
        order = Order.__table__()
        return (line
            .join(order, condition=line.order == order.id)
            .select(
                Min(line.id).as_('id'),
                Min(line.create_uid).as_('create_uid'),
                Min(line.create_date).as_('create_date'),
                Max(line.write_uid).as_('write_uid'),
                Max(line.write_date).as_('write_date'),
                order.company.as_('company'),
                order.restaurant.as_('restaurant'),
                order.date.as_('date'),
                order.type.as_('type'),
                line.product.as_('product'),
                Sum(line.quantity).as_('quantity'),
                Sum(line.quantity * line.unit_price
                    * (Literal(100) - Coalesce(line.discount_percent, 0)) / 100).as_('amount'),
                where=(order.state == 'paid') & (line.state != 'cancelled'),
                group_by=[order.company, order.restaurant, order.date,
                    order.type, line.product]))


class Dashboard(ModelSQL, ModelView):
    "Restaurant Dashboard"
    __name__ = 'restaurant.dashboard'
    _rec_name = 'name'

    name = fields.Char("Name", readonly=True,
        help="Name displayed in lists and searches.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", readonly=True,
        help="Restaurant concerned.")
    company = fields.Many2One('company.company', "Company", readonly=True,
        help="Company that owns the record.")
    open_orders = fields.Function(fields.Integer("Open Orders",
        help="Orders not yet paid or cancelled."), 'get_stats')
    occupied_tables = fields.Function(
        fields.Integer("Occupied Tables",
        help="Tables with an open order."), 'get_stats')
    free_tables = fields.Function(fields.Integer("Free Tables",
        help="Tables without open order."), 'get_stats')
    kitchen_queue = fields.Function(
        fields.Integer("In the Kitchen",
        help="Items sent to the kitchen and not served yet."), 'get_stats')
    bar_queue = fields.Function(fields.Integer("At the Bar",
        help="Items sent to the bar and not served yet."), 'get_stats')
    deliveries = fields.Function(
        fields.Integer("Deliveries in Progress",
        help="Orders being delivered at this moment."), 'get_stats')
    reservations = fields.Function(
        fields.Integer("Reservations Today",
        help="Bookings expected today."), 'get_stats')
    sales_today = fields.Function(
        fields.Numeric("Sales Today", digits=(16, 2),
        help="Sales of the day."), 'get_stats')

    @classmethod
    def table_query(cls):
        restaurant = Pool().get('restaurant.restaurant').__table__()
        return restaurant.select(
            restaurant.id.as_('id'),
            restaurant.create_uid.as_('create_uid'),
            restaurant.create_date.as_('create_date'),
            restaurant.write_uid.as_('write_uid'),
            restaurant.write_date.as_('write_date'),
            restaurant.name.as_('name'),
            restaurant.id.as_('restaurant'),
            restaurant.company.as_('company'),
            where=restaurant.active)

    @classmethod
    def get_stats(cls, dashboards, names):
        pool = Pool()
        Order = pool.get('restaurant.order')
        Table = pool.get('restaurant.table')
        Line = pool.get('restaurant.order.line')
        Reservation = pool.get('restaurant.reservation')
        today = pool.get('ir.date').today()
        result = {name: {} for name in names}
        for d in dashboards:
            r = d.restaurant.id
            tables = Table.search_count([('restaurant', '=', r)])
            busy = Table.search_count([
                    ('restaurant', '=', r), ('status', '=', 'occupied')])
            paid = Order.search([
                    ('restaurant', '=', r), ('date', '=', today),
                    ('state', '=', 'paid')])
            queue = lambda station: Line.search_count([
                    ('order.restaurant', '=', r), ('station', '=', station),
                    ('state', 'in', ['sent', 'preparing'])])
            values = {
                'open_orders': Order.search_count([
                        ('restaurant', '=', r), ('state', '=', 'open')]),
                'occupied_tables': busy,
                'free_tables': tables - busy,
                'kitchen_queue': queue('kitchen'),
                'bar_queue': queue('bar'),
                'deliveries': Order.search_count([
                        ('restaurant', '=', r), ('state', '=', 'open'),
                        ('type', '=', 'delivery'),
                        ('delivery_state', 'in', ['assigned', 'on_the_way'])]),
                'reservations': Reservation.search_count([
                        ('restaurant', '=', r), ('date', '=', today),
                        ('state', '=', 'booked')]),
                'sales_today': sum((o.total for o in paid), _ZERO),
                }
            for name in names:
                result[name][d.id] = values[name]
        return result


class OrderReceipt(Report):
    "Bill / receipt"
    __name__ = 'restaurant.order.receipt'


class OrderTicket(Report):
    "Thermal ticket (80 mm)"
    __name__ = 'restaurant.order.ticket'


class OrderSplitBill(Report):
    "Bill split by guest"
    __name__ = 'restaurant.order.split'
