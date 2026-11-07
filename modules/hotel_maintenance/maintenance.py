# This file is part of the Tryton hotel_maintenance module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Workflow, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import MaintenanceError

PRIORITIES = [
    ('low', "Low"),
    ('normal', "Normal"),
    ('high', "High"),
    ('urgent', "Urgent"),
    ]


class _HotelCompany:
    "Company of the hotel, usable in the access rules"
    __slots__ = ()

    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')

    def get_company(self, name):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]


def _next_number(code):
    pool = Pool()
    Sequence = pool.get('ir.sequence')
    ModelData = pool.get('ir.model.data')
    return Sequence(ModelData.get_id('hotel_maintenance', code)).get()


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    laundry_location = fields.Many2One(
        'stock.location', "Laundry Location",
        domain=[('type', '=', 'storage')],
        help="Where the linen sent to the laundry is stored meanwhile.")


class Order(Workflow, ModelSQL, ModelView, _HotelCompany):
    "Hotel Maintenance Work Order"
    __name__ = 'hotel.maintenance.order'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        states={'readonly': Eval('state') != 'open'})
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'get_currency_digits')
    room = fields.Many2One(
        'hotel.room', "Room",
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': ~Eval('state').in_(['open', 'assigned'])})
    location = fields.Char(
        "Location", help="Place of the work when it is not a room.")
    title = fields.Char("Title", required=True)
    description = fields.Text("Description")
    priority = fields.Selection(PRIORITIES, "Priority", required=True)
    state = fields.Selection([
            ('open', "Open"),
            ('assigned', "Assigned"),
            ('in_progress', "In Progress"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'open', 'warning',
                        If(Eval('state') == 'in_progress', 'success',
                        If(Eval('state') == 'done', 'muted',
                        If(Eval('state') == 'cancelled', 'muted',
                        ''))))),
            ]
    assigned_to = fields.Many2One('res.user', "Assigned To")
    requested_by = fields.Many2One('res.user', "Requested By")
    planned_date = fields.Date("Planned Date")
    started_at = fields.DateTime("Started At", readonly=True)
    finished_at = fields.DateTime("Finished At", readonly=True)
    cost = fields.Numeric(
        "Cost", digits=(16, Eval('currency_digits', 2)),
        domain=[('cost', '>=', 0)])
    block_room = fields.Boolean(
        "Block Room",
        states={'invisible': ~Bool(Eval('room'))},
        help="Take the room out of service while the work is in progress.")
    schedule = fields.Many2One(
        'hotel.maintenance.schedule', "Schedule", readonly=True,
        ondelete='SET NULL')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('planned_date', 'ASC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {
            ('open', 'assigned'),
            ('open', 'in_progress'),
            ('assigned', 'open'),
            ('assigned', 'in_progress'),
            ('in_progress', 'done'),
            ('open', 'cancelled'),
            ('assigned', 'cancelled'),
            ('in_progress', 'cancelled'),
            ('cancelled', 'open'),
            }
        cls._buttons.update({
                'assign': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                'start': {
                    'invisible': ~Eval('state').in_(['open', 'assigned']),
                    'depends': ['state'],
                    },
                'finish': {
                    'invisible': Eval('state') != 'in_progress',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': ~Eval('state').in_(
                        ['open', 'assigned', 'in_progress']),
                    'depends': ['state'],
                    },
                'reopen': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_priority(cls):
        return 'normal'

    @classmethod
    def default_state(cls):
        return 'open'

    @classmethod
    def default_requested_by(cls):
        return Transaction().user

    @classmethod
    def default_block_room(cls):
        return False

    @classmethod
    def default_hotel(cls):
        hotels = Pool().get('hotel.hotel').search([], limit=2)
        return hotels[0].id if len(hotels) == 1 else None

    def get_currency_digits(self, name):
        if self.hotel:
            return self.hotel.company.currency.digits
        return 2

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = _next_number('sequence_order')
        return super().create(vlist)

    @classmethod
    def copy(cls, orders, default=None):
        default = {} if default is None else default.copy()
        for name in ['number', 'started_at', 'finished_at', 'schedule']:
            default.setdefault(name, None)
        return super().copy(orders, default=default)

    @classmethod
    def delete(cls, orders):
        for order in orders:
            if order.state in {'in_progress', 'done'}:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_order_delete_state',
                        order=order.rec_name))
        super().delete(orders)

    @classmethod
    def validate(cls, orders):
        super().validate(orders)
        for order in orders:
            if order.room and order.room.hotel != order.hotel:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_order_room_hotel',
                        order=order.rec_name, room=order.room.rec_name))

    @classmethod
    def _release_rooms(cls, orders):
        "Send back to cleaning the rooms blocked by the orders"
        Room = Pool().get('hotel.room')
        rooms = [
            o.room for o in orders if o.block_room and o.room
            and o.room.state in {'maintenance', 'out_of_order'}]
        if rooms:
            Room.write(rooms, {'state': 'dirty'})

    @classmethod
    @ModelView.button
    @Workflow.transition('assigned')
    def assign(cls, orders):
        for order in orders:
            if not order.assigned_to:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_order_no_assignee',
                        order=order.rec_name))

    @classmethod
    @ModelView.button
    @Workflow.transition('in_progress')
    def start(cls, orders):
        Room = Pool().get('hotel.room')
        for order in orders:
            if order.block_room and order.room:
                if order.room.state == 'occupied':
                    raise MaintenanceError(gettext(
                            'hotel_maintenance.msg_order_room_occupied',
                            order=order.rec_name, room=order.room.rec_name))
                Room.write([order.room], {
                        'state': ('out_of_order' if order.priority == 'urgent'
                            else 'maintenance')})
        cls.write(orders, {'started_at': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def finish(cls, orders):
        cls._release_rooms(orders)
        cls.write(orders, {'finished_at': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, orders):
        cls._release_rooms([o for o in orders if o.state == 'in_progress'])

    @classmethod
    @ModelView.button
    @Workflow.transition('open')
    def reopen(cls, orders):
        cls.write(orders, {'started_at': None, 'finished_at': None})


class Schedule(DeactivableMixin, ModelSQL, ModelView, _HotelCompany):
    "Preventive Maintenance Schedule"
    __name__ = 'hotel.maintenance.schedule'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    room = fields.Many2One(
        'hotel.room', "Room", domain=[('hotel', '=', Eval('hotel', -1))])
    title = fields.Char("Title", required=True)
    description = fields.Text("Description")
    priority = fields.Selection(PRIORITIES, "Priority", required=True)
    every_days = fields.Integer(
        "Every (days)", required=True, domain=[('every_days', '>=', 1)])
    next_date = fields.Date("Next Date", required=True)
    block_room = fields.Boolean("Block Room")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('next_date', 'ASC'))

    @classmethod
    def default_priority(cls):
        return 'normal'

    @classmethod
    def default_every_days(cls):
        return 30

    @classmethod
    def default_next_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_block_room(cls):
        return False

    @classmethod
    def generate(cls, date=None):
        "Create the orders due and move the schedules on (cron entry point)"
        pool = Pool()
        Order = pool.get('hotel.maintenance.order')
        if date is None:
            date = pool.get('ir.date').today()
        orders = []
        schedules = cls.search([('next_date', '<=', date)])
        for schedule in schedules:
            existing = Order.search([
                    ('schedule', '=', schedule.id),
                    ('planned_date', '=', schedule.next_date)], limit=1)
            if not existing:
                orders.append({
                        'hotel': schedule.hotel.id,
                        'room': schedule.room.id if schedule.room else None,
                        'title': schedule.title,
                        'description': schedule.description,
                        'priority': schedule.priority,
                        'planned_date': schedule.next_date,
                        'block_room': schedule.block_room,
                        'requested_by': None,
                        'schedule': schedule.id,
                        })
            next_date = schedule.next_date
            while next_date <= date:
                next_date += datetime.timedelta(days=schedule.every_days)
            schedule.next_date = next_date
        cls.save(schedules)
        return Order.create(orders) if orders else []


class LostItem(Workflow, ModelSQL, ModelView, _HotelCompany):
    "Lost and Found Item"
    __name__ = 'hotel.lost.item'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        states={'readonly': Eval('state') != 'stored'})
    description = fields.Text("Description", required=True)
    found_date = fields.Date("Found On", required=True)
    room = fields.Many2One(
        'hotel.room', "Room", domain=[('hotel', '=', Eval('hotel', -1))])
    found_by = fields.Char("Found By")
    state = fields.Selection([
            ('stored', "Stored"),
            ('claimed', "Claimed"),
            ('disposed', "Disposed"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'stored', 'warning',
                        If(Eval('state') == 'claimed', 'muted',
                        If(Eval('state') == 'disposed', 'muted',
                        '')))),
            ]
    claimed_by = fields.Many2One(
        'party.party', "Claimed By",
        context={'company': Eval('company', -1)}, depends=['company'],
        states={'required': Eval('state') == 'claimed'})
    claim_date = fields.Date("Claim Date", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('found_date', 'DESC'))
        cls._transitions |= {
            ('stored', 'claimed'),
            ('stored', 'disposed'),
            }
        cls._buttons.update({
                'claim': {
                    'invisible': Eval('state') != 'stored',
                    'depends': ['state'],
                    },
                'dispose': {
                    'invisible': Eval('state') != 'stored',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_found_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'stored'

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = _next_number('sequence_lost_item')
        return super().create(vlist)

    @classmethod
    def copy(cls, items, default=None):
        default = {} if default is None else default.copy()
        for name in ['number', 'claimed_by', 'claim_date']:
            default.setdefault(name, None)
        return super().copy(items, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('claimed')
    def claim(cls, items):
        for item in items:
            if not item.claimed_by:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_lost_no_claimant',
                        item=item.rec_name))
        cls.write(items, {'claim_date': Pool().get('ir.date').today()})

    @classmethod
    @ModelView.button
    @Workflow.transition('disposed')
    def dispose(cls, items):
        pass


class LaundryBatch(Workflow, ModelSQL, ModelView, _HotelCompany):
    "Laundry Batch"
    __name__ = 'hotel.laundry.batch'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        states={'readonly': Eval('state') != 'draft'})
    supplier = fields.Many2One(
        'party.party', "Laundry", required=True,
        context={'company': Eval('company', -1)}, depends=['company'],
        states={'readonly': Eval('state') != 'draft'})
    date = fields.Date(
        "Date", required=True, states={'readonly': Eval('state') != 'draft'})
    lines = fields.One2Many(
        'hotel.laundry.batch.line', 'batch', "Lines",
        states={'readonly': Eval('state') == 'received'})
    notes = fields.Text("Notes")
    state = fields.Selection([
            ('draft', "Draft"),
            ('sent', "Sent"),
            ('received', "Received"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'received', 'muted',
                        ''))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {
            ('draft', 'sent'),
            ('sent', 'received'),
            }
        cls._buttons.update({
                'send': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'receive': {
                    'invisible': Eval('state') != 'sent',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = _next_number('sequence_laundry')
        return super().create(vlist)

    @classmethod
    def copy(cls, batches, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        return super().copy(batches, default=default)

    @classmethod
    def delete(cls, batches):
        for batch in batches:
            if batch.state != 'draft':
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_laundry_delete_state',
                        batch=batch.rec_name))
        super().delete(batches)

    @classmethod
    @ModelView.button
    @Workflow.transition('sent')
    def send(cls, batches):
        pool = Pool()
        Move = pool.get('stock.move')
        Line = pool.get('hotel.laundry.batch.line')
        lines, moves = [], []
        for batch in batches:
            if not batch.lines:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_laundry_no_lines',
                        batch=batch.rec_name))
            if not batch.hotel.laundry_location:
                raise MaintenanceError(gettext(
                        'hotel_maintenance.msg_laundry_no_location',
                        hotel=batch.hotel.rec_name))
            for line in batch.lines:
                line.send_move = line.get_move(
                    batch.hotel.warehouse.storage_location,
                    batch.hotel.laundry_location, line.qty_sent)
                lines.append(line)
                moves.append(line.send_move)
        Move.save(moves)
        Line.save(lines)
        Move.do(moves)

    @classmethod
    @ModelView.button
    @Workflow.transition('received')
    def receive(cls, batches):
        pool = Pool()
        Move = pool.get('stock.move')
        Line = pool.get('hotel.laundry.batch.line')
        lines, moves = [], []
        for batch in batches:
            for line in batch.lines:
                if line.qty_returned is None:
                    line.qty_returned = line.qty_sent
                if not 0 <= line.qty_returned <= line.qty_sent:
                    raise MaintenanceError(gettext(
                            'hotel_maintenance.msg_laundry_returned_more',
                            batch=batch.rec_name,
                            product=line.product.rec_name))
                if line.qty_returned > 0:
                    line.return_move = line.get_move(
                        batch.hotel.laundry_location,
                        batch.hotel.warehouse.storage_location,
                        line.qty_returned)
                    moves.append(line.return_move)
                lines.append(line)
        Move.save(moves)
        Line.save(lines)
        if moves:
            Move.do(moves)


class LaundryBatchLine(ModelSQL, ModelView):
    "Laundry Batch Line"
    __name__ = 'hotel.laundry.batch.line'

    batch = fields.Many2One(
        'hotel.laundry.batch', "Batch", required=True, ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[
            ('template.hotel_article', '=', True),
            ('type', '=', 'goods'),
            ])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', "Product UoM Category"),
        'on_change_with_product_uom_category')
    unit = fields.Many2One(
        'product.uom', "Unit", required=True,
        domain=[('category', '=', Eval('product_uom_category', -1))])
    qty_sent = fields.Float(
        "Sent", digits='unit', required=True, domain=[('qty_sent', '>', 0)])
    qty_returned = fields.Float(
        "Returned", digits='unit',
        help="Empty means everything comes back.")
    lost = fields.Function(
        fields.Float("Lost", digits='unit'), 'get_lost')
    send_move = fields.Many2One('stock.move', "Sent Move", readonly=True)
    return_move = fields.Many2One('stock.move', "Return Move", readonly=True)

    @classmethod
    def default_qty_sent(cls):
        return 1.

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('product', 'unit')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.default_uom

    def get_lost(self, name):
        if self.batch and self.batch.state == 'received':
            return (self.qty_sent or 0.) - (self.qty_returned or 0.)
        return 0.

    @classmethod
    def copy(cls, lines, default=None):
        default = {} if default is None else default.copy()
        for name in ['send_move', 'return_move', 'qty_returned']:
            default.setdefault(name, None)
        return super().copy(lines, default=default)

    def get_move(self, from_location, to_location, quantity):
        pool = Pool()
        Move = pool.get('stock.move')
        batch = self.batch
        move = Move()
        move.company = batch.hotel.company
        move.currency = batch.hotel.company.currency
        move.product = self.product
        move.unit = self.unit
        move.quantity = quantity
        move.from_location = from_location
        move.to_location = to_location
        move.effective_date = batch.date
        move.origin = self
        if move.on_change_with_unit_price_required():
            move.unit_price = self.product.cost_price or Decimal(0)
        return move


class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['hotel.laundry.batch.line']


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('hotel.maintenance.schedule|generate',
                "Generate Preventive Maintenance Orders"))
