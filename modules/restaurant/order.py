# This file is part of the Tryton restaurant module.
import datetime
from collections import defaultdict
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.modules.product import price_digits
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction, without_check_access

from .exceptions import (
    CashError, DeliveryError, OrderError, RestaurantWarning)

_ZERO = Decimal(0)
ORDER_TYPES = [
    ('dine_in', "Dine-in"),
    ('bar_tab', "Bar Tab"),
    ('takeaway', "Takeaway"),
    ('delivery', "Delivery"),
    ]
LINE_STATES = [
    ('new', "New"),
    ('sent', "Sent"),
    ('preparing', "Preparing"),
    ('ready', "Ready"),
    ('served', "Served"),
    ('cancelled', "Cancelled"),
    ]
DELIVERY_STATES = [
    ('pending', "Pending"),
    ('assigned', "Assigned"),
    ('on_the_way', "On the Way"),
    ('delivered', "Delivered"),
    ('failed', "Failed"),
    ]
PAYMENT_METHODS = [
    ('cash', "Cash"),
    ('card', "Card"),
    ('mobile', "Mobile Money"),
    ('voucher', "Voucher"),
    ('account', "On Account"),
    ]


def _require_manager():
    pool = Pool()
    user = Transaction().user
    if user == 0:
        return
    group = pool.get('ir.model.data').get_id(
        'restaurant', 'group_restaurant_manager')
    if group not in pool.get('res.user').get_groups():
        raise OrderError(gettext('restaurant.msg_manager_required'))


def _is_manager():
    try:
        _require_manager()
        return True
    except OrderError:
        return False


class Order(Workflow, ModelSQL, ModelView):
    "Restaurant Order"
    __name__ = 'restaurant.order'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'open'},
        help="Company that owns the record.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': (Eval('state') != 'open')
            | Bool(Eval('lines', []))},
        help="Restaurant concerned.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    type = fields.Selection(
        ORDER_TYPES, "Type", required=True,
        states={'readonly': (Eval('state') != 'open')
            | Bool(Eval('lines', []))},
        help="Kind of record.")
    table = fields.Many2One(
        'restaurant.table', "Table",
        domain=[('restaurant', '=', Eval('restaurant', -1))],
        states={'required': Eval('type') == 'dine_in',
            'invisible': Eval('type') != 'dine_in',
            'readonly': Eval('state') != 'open'},
        help="Table of the restaurant.")
    tab_name = fields.Char(
        "Name",
        states={'invisible': Eval('type').in_(['dine_in', 'delivery']),
            'readonly': Eval('state') != 'open'},
        help="Name of the bar tab or of the takeaway customer.")
    party = fields.Many2One(
        'party.party', "Customer",
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'open'},
        help="Customer or contact concerned.")
    covers = fields.Integer(
        "Covers", domain=[('covers', '>=', 0)],
        states={'readonly': Eval('state') != 'open'},
        help="Number of guests at the table.")
    waiter = fields.Many2One(
        'company.employee', "Waiter",
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'open'},
        help="Employee who takes care of the table.")
    date = fields.Date(
        "Date", required=True,
        states={'readonly': Eval('state') != 'open'},
        help="Date of the document.")
    state = fields.Selection([
            ('open', "Open"),
            ('paid', "Paid"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True,
        help="Current step of the workflow.")
    lines = fields.One2Many(
        'restaurant.order.line', 'order', "Items",
        states={'readonly': Eval('state') != 'open'},
        help="Detail lines of the document.")
    payments = fields.One2Many(
        'restaurant.payment', 'order', "Payments",
        states={'readonly': Eval('state') != 'open'},
        help="Payments received for this record.")
    discount_percent = fields.Numeric(
        "Discount (%)", digits=(5, 2),
        domain=[('discount_percent', '>=', 0),
            ('discount_percent', '<=', 100)],
        states={'readonly': Eval('state') != 'open'},
        help="Discount rate in percent, including the happy hour discount.")
    tip = fields.Numeric(
        "Tip", digits='currency', domain=[('tip', '>=', 0)],
        states={'readonly': Eval('state') != 'open'},
        help="Tip left by the customer, added to the balance.")
    # Delivery
    delivery_address = fields.Text(
        "Delivery Address",
        states={'invisible': Eval('type') != 'delivery',
            'required': Eval('type') == 'delivery',
            'readonly': Eval('state') != 'open'},
        help="Address where the order must be delivered.")
    phone = fields.Char(
        "Phone",
        states={'invisible': Eval('type') != 'delivery',
            'required': Eval('type') == 'delivery',
            'readonly': Eval('state') != 'open'},
        help="Phone number to call for this record.")
    driver = fields.Many2One(
        'restaurant.driver', "Driver",
        states={'invisible': Eval('type') != 'delivery'},
        help="Person in charge of the delivery.")
    delivery_state = fields.Selection(
        [(None, "")] + DELIVERY_STATES, "Delivery Status", readonly=True,
        states={'invisible': Eval('type') != 'delivery'},
        help="Progress of the delivery.")
    notes = fields.Text("Notes",
        help="Free text for internal use.")
    # Totals
    subtotal = fields.Function(
        fields.Numeric("Subtotal", digits='currency',
        help="Total of the items before discount, service and delivery."), 'get_totals')
    discount_amount = fields.Function(
        fields.Numeric("Discount", digits='currency',
        help="Amount of the discount."), 'get_totals')
    service_amount = fields.Function(
        fields.Numeric("Service", digits='currency',
        help="Service charge added to table and bar orders."), 'get_totals')
    delivery_amount = fields.Function(
        fields.Numeric("Delivery Fee", digits='currency',
        help="Delivery fee added to the order."), 'get_totals')
    total = fields.Function(
        fields.Numeric("Total", digits='currency',
        help="Total to pay."), 'get_totals')
    tax_amount = fields.Function(
        fields.Numeric("Tax Included", digits='currency',
        help="Tax contained in the total (informative)."), 'get_totals')
    paid_amount = fields.Function(
        fields.Numeric("Paid", digits='currency',
        help="Total of the payments received."), 'get_totals')
    balance = fields.Function(
        fields.Numeric("Balance Due", digits='currency',
        help="Amount still to be paid, tip included."), 'get_totals')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {('open', 'paid'), ('open', 'cancelled')}
        cls._buttons.update({
                'send': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                'pay': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                'cancel': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                'dispatch': {
                    'invisible': (Eval('type') != 'delivery')
                    | ~Eval('delivery_state').in_(['pending', 'assigned']),
                    'depends': ['type', 'delivery_state']},
                'deliver': {
                    'invisible': (Eval('type') != 'delivery')
                    | (Eval('delivery_state') != 'on_the_way'),
                    'depends': ['type', 'delivery_state']},
                'delivery_failed': {
                    'invisible': (Eval('type') != 'delivery')
                    | (Eval('delivery_state') != 'on_the_way'),
                    'depends': ['type', 'delivery_state']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state') == 'cancelled', 'muted',
                    If(Eval('state') == 'paid', 'success',
                        If(Eval('delivery_state') == 'failed', 'danger',
                            'warning')))),
            ]

    # Defaults

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_restaurant(cls):
        Restaurant = Pool().get('restaurant.restaurant')
        rs = Restaurant.search([
                ('company', '=', cls.default_company())], limit=2)
        if len(rs) == 1:
            return rs[0].id

    @classmethod
    def default_type(cls):
        return 'dine_in'

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'open'

    @classmethod
    def default_discount_percent(cls):
        return _ZERO

    @classmethod
    def default_tip(cls):
        return _ZERO

    @classmethod
    def default_waiter(cls):
        user = Pool().get('res.user')(Transaction().user)
        if user.employee:
            return user.employee.id

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @fields.depends('type', 'table', 'delivery_state')
    def on_change_type(self):
        if self.type != 'dine_in':
            self.table = None
        self.delivery_state = 'pending' if self.type == 'delivery' else None

    @fields.depends('party', 'delivery_address', 'phone')
    def on_change_party(self):
        if self.party:
            address = self.party.address_get()
            if address and not self.delivery_address:
                self.delivery_address = address.full_address
            if not self.phone and self.party.phone:
                self.phone = self.party.phone

    # CRUD

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id(
                'restaurant', 'sequence_order'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
            if values.get('type') == 'delivery':
                values.setdefault('delivery_state', 'pending')
        cls._check_discount_values(vlist)
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        to_assign = []
        for orders, values in zip(actions, actions):
            if 'discount_percent' in values:
                cls._check_discount_values(
                    [dict(values, restaurant=o.restaurant.id)
                        for o in orders])
            if values.get('driver'):
                to_assign += [
                    o for o in orders if o.delivery_state == 'pending']
        super().write(*args)
        if to_assign:
            super().write(to_assign, {'delivery_state': 'assigned'})

    @classmethod
    def _check_discount_values(cls, vlist):
        Restaurant = Pool().get('restaurant.restaurant')
        for values in vlist:
            pct = values.get('discount_percent') or _ZERO
            if pct and values.get('restaurant'):
                maximum = Restaurant(values['restaurant']).max_discount
                if pct > maximum and not _is_manager():
                    raise OrderError(gettext(
                            'restaurant.msg_discount_too_high',
                            maximum=maximum))

    @classmethod
    def delete(cls, orders):
        for order in orders:
            if (order.state != 'open' or order.payments
                    or any(l.state != 'new' for l in order.lines)):
                raise OrderError(gettext(
                        'restaurant.msg_order_delete', order=order.rec_name))
        super().delete(orders)

    @classmethod
    def copy(cls, orders, default=None):
        default = {} if default is None else default.copy()
        for name in ('number', 'lines', 'payments', 'driver'):
            default.setdefault(name, None)
        default.setdefault('state', 'open')
        default.setdefault('tip', _ZERO)
        return super().copy(orders, default=default)

    @classmethod
    def validate(cls, orders):
        super().validate(orders)
        for order in orders:
            if order.restaurant.company != order.company:
                raise OrderError(gettext(
                        'restaurant.msg_order_company',
                        order=order.rec_name))
            if order.state == 'open' and order.table:
                other = cls.search([
                        ('table', '=', order.table.id),
                        ('state', '=', 'open'),
                        ('id', '!=', order.id)], limit=1)
                if other:
                    raise OrderError(gettext(
                            'restaurant.msg_order_table_busy',
                            table=order.table.rec_name,
                            order=other[0].rec_name))

    # Totals

    @classmethod
    def get_totals(cls, orders, names):
        result = {name: {} for name in names}
        for order in orders:
            r = order.restaurant
            cur = order.company.currency
            subtotal = sum(
                (l.amount or _ZERO for l in order.lines
                    if l.state != 'cancelled'), _ZERO)
            discount = cur.round(
                subtotal * (order.discount_percent or _ZERO) / 100)
            net = subtotal - discount
            service = _ZERO
            if order.type in {'dine_in', 'bar_tab'}:
                service = cur.round(net * r.service_rate / 100)
            delivery = _ZERO
            if order.type == 'delivery' and net > 0:
                free = r.free_delivery_amount
                if not (free and net >= free):
                    delivery = r.delivery_fee
            total = net + service + delivery
            tax = cur.round(total * r.tax_rate / (100 + r.tax_rate))
            paid = sum((p.amount for p in order.payments), _ZERO)
            values = {
                'subtotal': subtotal, 'discount_amount': discount,
                'service_amount': service, 'delivery_amount': delivery,
                'total': total, 'tax_amount': tax, 'paid_amount': paid,
                'balance': total + (order.tip or _ZERO) - paid,
                }
            for name in names:
                result[name][order.id] = values[name]
        return result

    # Workflow

    @classmethod
    @ModelView.button
    def send(cls, orders):
        "Send the new items to the kitchen and the bar"
        Line = Pool().get('restaurant.order.line')
        Warning = Pool().get('res.user.warning')
        for order in orders:
            if order.state != 'open':
                raise OrderError(gettext(
                        'restaurant.msg_order_not_open',
                        order=order.rec_name))
            new = [l for l in order.lines if l.state == 'new']
            if not new:
                raise OrderError(gettext(
                        'restaurant.msg_order_nothing_to_send',
                        order=order.rec_name))
            if order.type == 'delivery':
                order._check_delivery()
            available = None
            if order.restaurant.enforce_menus:
                available = Pool().get('restaurant.menu').available(
                    order.restaurant)
            for line in new:
                if available is not None and (
                        line.product.template.id not in available):
                    raise OrderError(gettext(
                            'restaurant.msg_line_not_on_menu',
                            product=line.product.rec_name))
                if line.product.template.sold_out:
                    raise OrderError(gettext(
                            'restaurant.msg_line_sold_out',
                            product=line.product.rec_name))
            if order.type == 'dine_in' and order.table and order.covers:
                if order.covers > order.table.seats:
                    key = 'restaurant_covers_%s' % order.id
                    if Warning.check(key):
                        raise RestaurantWarning(key, gettext(
                                'restaurant.msg_order_covers',
                                covers=order.covers,
                                seats=order.table.seats))
            Line.write(new, {
                    'state': 'sent', 'sent_at': datetime.datetime.now()})

    def guest_summary(self):
        """Per guest: items, subtotal, share of service, discount, total,
        paid and balance. Guest 0 (shared) is split evenly among the guests."""
        cur = self.company.currency
        r = self.restaurant
        lines = [l for l in self.lines if l.state != 'cancelled']
        guests = sorted({l.guest for l in lines if l.guest}
            | {p.guest for p in self.payments if p.guest})
        if not guests:
            guests = [0]
        shared = sum((l.amount for l in lines if not l.guest), _ZERO)
        rows = []
        remaining_shared = shared
        for i, g in enumerate(guests):
            own = [l for l in lines if l.guest == g] if g else lines
            subtotal = sum((l.amount for l in own), _ZERO) if g else (
                sum((l.amount for l in lines), _ZERO))
            if g:
                share = cur.round(shared / len(guests)) if (
                    i < len(guests) - 1) else remaining_shared
                remaining_shared -= share if i < len(guests) - 1 else 0
                subtotal += share
            discount = cur.round(
                subtotal * (self.discount_percent or _ZERO) / 100)
            net = subtotal - discount
            service = _ZERO
            if self.type in {'dine_in', 'bar_tab'}:
                service = cur.round(net * r.service_rate / 100)
            paid = sum((p.amount for p in self.payments if p.guest == g),
                _ZERO) if g else sum(
                (p.amount for p in self.payments), _ZERO)
            rows.append({
                    'guest': g, 'items': own, 'subtotal': subtotal,
                    'discount': discount, 'service': service,
                    'total': net + service, 'paid': paid,
                    'balance': net + service - paid})
        return rows

    def _check_delivery(self):
        r = self.restaurant
        net = self.subtotal - self.discount_amount
        if net < r.min_delivery_amount:
            raise DeliveryError(gettext(
                    'restaurant.msg_delivery_minimum',
                    minimum=r.min_delivery_amount))
        if not self.delivery_address or not self.phone:
            raise DeliveryError(gettext(
                    'restaurant.msg_delivery_address',
                    order=self.rec_name))

    @classmethod
    @ModelView.button
    @Workflow.transition('paid')
    def pay(cls, orders):
        Line = Pool().get('restaurant.order.line')
        for order in orders:
            lines = [l for l in order.lines if l.state != 'cancelled']
            if not lines:
                raise OrderError(gettext(
                        'restaurant.msg_order_empty', order=order.rec_name))
            if order.type == 'delivery':
                order._check_delivery()
            if order.balance > 0:
                raise OrderError(gettext(
                        'restaurant.msg_order_unpaid',
                        order=order.rec_name, balance=order.balance))
            Line.write(lines, {'state': 'served'})
            order._consume_stock()

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, orders):
        Line = Pool().get('restaurant.order.line')
        for order in orders:
            if order.payments:
                raise OrderError(gettext(
                        'restaurant.msg_order_cancel_paid',
                        order=order.rec_name))
            if any(l.state in {'preparing', 'ready', 'served'}
                    for l in order.lines):
                _require_manager()
            elif any(l.state == 'sent' for l in order.lines):
                _require_manager()
            Line.write([l for l in order.lines], {'state': 'cancelled'})

    @classmethod
    @ModelView.button
    def dispatch(cls, orders):
        for order in orders:
            if not order.driver:
                raise DeliveryError(gettext(
                        'restaurant.msg_delivery_no_driver',
                        order=order.rec_name))
            if order.state != 'open' or not any(
                    l.state != 'new' for l in order.lines):
                raise DeliveryError(gettext(
                        'restaurant.msg_delivery_not_sent',
                        order=order.rec_name))
            super(Order, cls).write([order], {'delivery_state': 'on_the_way'})

    @classmethod
    @ModelView.button
    def deliver(cls, orders):
        for order in orders:
            super(Order, cls).write([order], {'delivery_state': 'delivered'})

    @classmethod
    @ModelView.button
    def delivery_failed(cls, orders):
        for order in orders:
            super(Order, cls).write([order], {'delivery_state': 'failed'})

    # Stock

    def _consume_stock(self):
        pool = Pool()
        Move = pool.get('stock.move')
        Location = pool.get('stock.location')
        warehouse = self.restaurant.warehouse
        if not warehouse or not warehouse.storage_location:
            return
        quantities = defaultdict(float)
        for line in self.lines:
            if line.state == 'cancelled':
                continue
            template = line.product.template
            if template.recipe:
                for ingredient in template.recipe[0].lines:
                    quantities[ingredient.product] += (
                        ingredient.quantity * line.quantity)
            elif template.track_stock:
                quantities[line.product] += line.quantity
        locations = Location.search([('type', '=', 'customer')], limit=1)
        if not quantities:
            return
        if not locations:
            raise OrderError(gettext('restaurant.msg_no_customer_location'))
        moves = []
        for product, quantity in quantities.items():
            quantity = product.default_uom.round(quantity)
            if quantity <= 0:
                continue
            move = Move()
            move.company = self.company
            move.currency = self.company.currency
            move.product = product
            move.unit = product.default_uom
            move.quantity = quantity
            move.from_location = warehouse.storage_location
            move.to_location = locations[0]
            move.effective_date = self.date
            move.unit_price = product.cost_price
            move.origin = self
            moves.append(move)
        if moves:
            with without_check_access():
                Move.save(moves)
                Move.do(moves)

    def get_rec_name(self, name):
        text = self.number or str(self.id)
        if self.table:
            text += " (%s)" % self.table.code
        elif self.tab_name:
            text += " (%s)" % self.tab_name
        return text

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR', ('number',) + tuple(clause[1:]),
            ('tab_name',) + tuple(clause[1:]),
            ('table.code',) + tuple(clause[1:]),
            ('party.rec_name',) + tuple(clause[1:])]


class OrderLine(ModelSQL, ModelView):
    "Restaurant Order Item"
    __name__ = 'restaurant.order.line'

    order = fields.Many2One(
        'restaurant.order', "Order", required=True, ondelete='CASCADE',
        help="Order concerned.")
    product = fields.Many2One(
        'product.product', "Item", required=True,
        domain=[('template.restaurant_item', '=', True)],
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'new'},
        help="Product concerned.")
    company = fields.Function(
        fields.Many2One('company.company', "Company",
        help="Company that owns the record."),
        'on_change_with_company', searcher='search_company')
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    course = fields.Function(
        fields.Selection([('', "")] + [
                c for c in [
                    ('starter', "Starter"), ('main', "Main Course"),
                    ('dessert', "Dessert"), ('drink', "Drink"),
                    ('other', "Other")]], "Course",
        help="Course of the menu in which the item is served."),
        'on_change_with_course')
    station = fields.Function(
        fields.Selection([
                ('kitchen', "Kitchen"), ('bar', "Bar"),
                ('none', "No Preparation")], "Preparation",
        help="Station concerned."),
        'on_change_with_station', searcher='search_station')
    quantity = fields.Float(
        "Quantity", digits=(16, 2), required=True,
        domain=[('quantity', '>', 0)],
        states={'readonly': Eval('state') != 'new'},
        help="Quantity, in the unit of measure.")
    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits, required=True,
        domain=[('unit_price', '>=', 0)],
        states={'readonly': Eval('state') != 'new'},
        help="Price of one unit before discount.")
    discount_percent = fields.Numeric(
        "Happy Hour (%)", digits=(5, 2), readonly=True,
        help="Discount rate in percent, including the happy hour discount.")
    amount = fields.Function(
        fields.Numeric("Amount", digits='currency',
        help="Amount in the currency of the document."), 'on_change_with_amount')
    note = fields.Char(
        "Note", help="Cooking, allergies, no ice...",
        states={'readonly': ~Eval('state').in_(['new', 'sent'])})
    guest = fields.Integer(
        "Guest", domain=[('guest', '>=', 0)],
        help="Seat or guest number, to split the bill (0 = shared).",
        states={'readonly': Eval('state') == 'cancelled'})
    state = fields.Selection(LINE_STATES, "State", readonly=True, required=True,
        help="Current step of the workflow.")
    sent_at = fields.DateTime("Sent at", readonly=True,
        help="Time the item was sent to the kitchen or bar.")
    table = fields.Function(
        fields.Many2One('restaurant.table', "Table",
        help="Table of the restaurant."), 'get_order_field',
        searcher='search_order_field')
    order_type = fields.Function(
        fields.Selection(ORDER_TYPES, "Order Type",
        help="Dine-in, bar tab, takeaway or delivery."), 'get_order_field')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('sent_at', 'ASC'))
        cls._order.insert(1, ('id', 'ASC'))
        cls._buttons.update({
                'start': {
                    'invisible': Eval('state') != 'sent',
                    'depends': ['state']},
                'ready': {
                    'invisible': ~Eval('state').in_(['sent', 'preparing']),
                    'depends': ['state']},
                'serve': {
                    'invisible': Eval('state') != 'ready',
                    'depends': ['state']},
                'cancel_line': {
                    'invisible': ~Eval('state').in_(
                        ['new', 'sent', 'preparing']),
                    'depends': ['state']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state') == 'cancelled', 'muted',
                    If(Eval('delay') == 'late', 'danger',
                        If(Eval('delay') == 'warning', 'warning',
                            If(Eval('state') == 'ready', 'success',
                                If(Eval('state') == 'preparing',
                                    'warning', '')))))),
            ]

    @classmethod
    def default_state(cls):
        return 'new'

    @classmethod
    def default_guest(cls):
        return 0

    @classmethod
    def default_quantity(cls):
        return 1.0

    @classmethod
    def default_discount_percent(cls):
        return _ZERO

    @fields.depends('order', '_parent_order.company')
    def on_change_with_company(self, name=None):
        if self.order and self.order.company:
            return self.order.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('order.company',) + tuple(clause[1:])]

    @fields.depends('order', '_parent_order.company')
    def on_change_with_currency(self, name=None):
        if self.order and self.order.company:
            return self.order.company.currency.id

    @fields.depends('product')
    def on_change_with_course(self, name=None):
        if self.product:
            return self.product.template.course
        return ''

    @fields.depends('product')
    def on_change_with_station(self, name=None):
        if self.product:
            return self.product.template.prep_station

    @classmethod
    def search_station(cls, name, clause):
        return [('product.template.prep_station',) + tuple(clause[1:])]

    def get_order_field(self, name):
        if name == 'table':
            return self.order.table.id if self.order.table else None
        return self.order.type

    @classmethod
    def search_order_field(cls, name, clause):
        return [('order.table',) + tuple(clause[1:])]

    def _price(self):
        "(unit price, happy hour discount) for the product now"
        pool = Pool()
        HappyHour = pool.get('restaurant.happy_hour')
        price = self.product.list_price_used or _ZERO
        restaurant = self.order.restaurant
        if restaurant.enforce_menus:
            menu_price = Pool().get('restaurant.menu').available(
                restaurant).get(self.product.template.id)
            if menu_price is not None:
                price = menu_price
        discount = HappyHour.get_discount(
            self.order.restaurant, self.product)
        return price, discount

    @fields.depends(
        'product', 'order', '_parent_order.restaurant', 'unit_price',
        'discount_percent')
    def on_change_product(self):
        if self.product and self.order and self.order.restaurant:
            self.unit_price, self.discount_percent = self._price()

    @fields.depends(
        'quantity', 'unit_price', 'discount_percent', 'order',
        '_parent_order.company')
    def on_change_with_amount(self, name=None):
        if self.unit_price is None or not self.quantity:
            return _ZERO
        gross = Decimal(str(round(self.quantity, 4))) * self.unit_price
        gross = gross * (100 - (self.discount_percent or _ZERO)) / 100
        if self.order and self.order.company:
            return self.order.company.currency.round(gross)
        return gross

    @classmethod
    def create(cls, vlist):
        Order = Pool().get('restaurant.order')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            order = Order(values['order'])
            if order.state != 'open':
                raise OrderError(gettext(
                        'restaurant.msg_order_not_open',
                        order=order.rec_name))
            if values.get('unit_price') is None:
                line = cls(product=values['product'], order=order)
                values['unit_price'], values['discount_percent'] = (
                    line._price())
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for lines, values in zip(actions, actions):
            for line in lines:
                if line.order.state != 'open' and (
                        set(values) - {
                            'state', 'sent_at', 'ready_at',
                            'notified_level'}):
                    raise OrderError(gettext(
                            'restaurant.msg_order_not_open',
                            order=line.order.rec_name))
                if (line.state not in {'new'}
                        and {'quantity', 'unit_price', 'product'}
                        & set(values)):
                    raise OrderError(gettext(
                            'restaurant.msg_line_sent',
                            product=line.product.rec_name))
        super().write(*args)

    @classmethod
    def delete(cls, lines):
        for line in lines:
            if line.state != 'new' or line.order.state != 'open':
                raise OrderError(gettext(
                        'restaurant.msg_line_sent',
                        product=line.product.rec_name))
        super().delete(lines)

    @classmethod
    @ModelView.button
    def start(cls, lines):
        cls._move(lines, ['sent'], 'preparing')

    @classmethod
    @ModelView.button
    def ready(cls, lines):
        cls._move(lines, ['sent', 'preparing'], 'ready')

    @classmethod
    @ModelView.button
    def serve(cls, lines):
        cls._move(lines, ['ready'], 'served')

    @classmethod
    @ModelView.button
    def cancel_line(cls, lines):
        for line in lines:
            if line.state != 'new':
                _require_manager()
        cls._move(lines, ['new', 'sent', 'preparing'], 'cancelled')

    @classmethod
    def _move(cls, lines, allowed, state):
        for line in lines:
            if line.state not in allowed:
                raise OrderError(gettext(
                        'restaurant.msg_line_state',
                        product=line.product.rec_name))
        with without_check_access():
            super(OrderLine, cls).write(lines, {'state': state})


class Payment(ModelSQL, ModelView):
    "Restaurant Payment"
    __name__ = 'restaurant.payment'

    order = fields.Many2One(
        'restaurant.order', "Order", required=True, ondelete='CASCADE',
        help="Order concerned.")
    method = fields.Selection(PAYMENT_METHODS, "Method", required=True,
        help="How the customer pays.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    amount = fields.Numeric(
        "Amount", digits='currency', required=True,
        domain=[('amount', '>', 0)],
        help="Amount in the currency of the document.")
    tendered = fields.Numeric(
        "Cash Given", digits='currency',
        domain=[['OR', ('tendered', '=', None), ('tendered', '>=', 0)]],
        states={'invisible': Eval('method') != 'cash'},
        help="Cash handed over by the customer.")
    change = fields.Function(
        fields.Numeric("Change", digits='currency',
        help="Money given back to the customer."), 'on_change_with_change')
    reference = fields.Char("Reference",
        help="Reference of the payment (receipt number, voucher...).")
    guest = fields.Integer(
        "Guest", domain=[('guest', '>=', 0)],
        help="Guest who pays this part of the bill (0 = whole table).")
    session = fields.Many2One(
        'restaurant.cash.session', "Cash Session", readonly=True,
        ondelete='RESTRICT',
        states={'invisible': Eval('method') != 'cash'},
        help="Cash session concerned.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('order', 'ASC'))
        cls._order.insert(1, ('id', 'ASC'))

    @classmethod
    def default_method(cls):
        return 'cash'

    @classmethod
    def default_guest(cls):
        return 0

    @fields.depends('order', '_parent_order.company')
    def on_change_with_currency(self, name=None):
        if self.order and self.order.company:
            return self.order.company.currency.id

    @fields.depends('method', 'tendered', 'amount')
    def on_change_with_change(self, name=None):
        if (self.method == 'cash' and self.tendered is not None
                and self.amount is not None
                and self.tendered > self.amount):
            return self.tendered - self.amount
        return _ZERO

    @fields.depends('order', 'amount', 'method',
        '_parent_order.balance')
    def on_change_method(self):
        if self.order and not self.amount and self.order.balance:
            self.amount = max(self.order.balance, _ZERO) or None

    @classmethod
    def validate(cls, payments):
        super().validate(payments)
        for payment in payments:
            if (payment.method == 'cash' and payment.tendered is not None
                    and payment.tendered < payment.amount):
                raise OrderError(gettext(
                        'restaurant.msg_payment_tendered',
                        order=payment.order.rec_name))
            if payment.method == 'account' and not payment.order.party:
                raise OrderError(gettext(
                        'restaurant.msg_payment_account',
                        order=payment.order.rec_name))

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Order = pool.get('restaurant.order')
        Session = pool.get('restaurant.cash.session')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            order = Order(values['order'])
            if order.state != 'open':
                raise OrderError(gettext(
                        'restaurant.msg_order_not_open',
                        order=order.rec_name))
            if (values.get('method', 'cash') == 'cash'
                    and order.restaurant.cash_control
                    and not values.get('session')):
                session = Session.get_current(order.restaurant)
                if not session:
                    raise CashError(gettext(
                            'restaurant.msg_payment_no_session',
                            restaurant=order.restaurant.rec_name))
                values['session'] = session.id
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for payments, _ in zip(actions, actions):
            for payment in payments:
                if payment.order.state != 'open':
                    raise OrderError(gettext(
                            'restaurant.msg_order_not_open',
                            order=payment.order.rec_name))
        super().write(*args)

    @classmethod
    def delete(cls, payments):
        for payment in payments:
            if payment.order.state != 'open':
                raise OrderError(gettext(
                        'restaurant.msg_order_not_open',
                        order=payment.order.rec_name))
        super().delete(payments)


class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['restaurant.order']
