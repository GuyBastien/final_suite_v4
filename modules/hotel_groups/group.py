# This file is part of the Tryton hotel_groups module.
import datetime

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.modules.product import price_digits, round_price
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import EventError, GroupError

_STATES = {'readonly': Eval('state') == 'cancelled'}


class Group(ModelSQL, ModelView):
    "Hotel Group"
    __name__ = 'hotel.group'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    name = fields.Char("Name", required=True, states=_STATES)
    company = fields.Many2One(
        'company.company', "Company", required=True, states=_STATES)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        domain=[('company', '=', Eval('company', -1))], states=_STATES)
    party = fields.Many2One(
        'party.party', "Organizer", required=True, states=_STATES,
        context={'company': Eval('company', -1)}, depends=['company'],
        help="The party invoiced and used as guest of the generated "
        "reservations.")
    start_date = fields.Date("Arrival", required=True, states=_STATES)
    end_date = fields.Date(
        "Departure", required=True,
        domain=[If(Bool(Eval('start_date')),
                ('end_date', '>', Eval('start_date')), ())],
        states=_STATES)
    cutoff_date = fields.Date(
        "Cut-off Date", states=_STATES,
        help="After this date the blocked rooms that were not picked up "
        "can be released.")
    blocks = fields.One2Many(
        'hotel.group.block', 'group', "Room Blocks", states=_STATES)
    reservations = fields.One2Many(
        'hotel.reservation', 'group', "Reservations", readonly=True)
    events = fields.One2Many('hotel.event', 'group', "Events")
    notes = fields.Text("Notes")
    state = fields.Selection([
            ('draft', "Draft"),
            ('definite', "Definite"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'definite', 'success',
                        If(Eval('state') == 'cancelled', 'muted',
                        '')))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('start_date', 'DESC'))
        cls._buttons.update({
                'draft': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                'definite': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': Eval('state') == 'cancelled',
                    'depends': ['state'],
                    },
                'generate_reservations': {
                    'invisible': Eval('state') == 'cancelled',
                    'depends': ['state'],
                    },
                'release': {
                    'invisible': Eval('state') != 'definite',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id('hotel_groups', 'sequence_group'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, groups, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        default.setdefault('reservations', None)
        default.setdefault('events', None)
        return super().copy(groups, default=default)

    @classmethod
    def validate(cls, groups):
        super().validate(groups)
        for group in groups:
            if (group.cutoff_date
                    and group.cutoff_date > group.start_date):
                raise GroupError(gettext(
                        'hotel_groups.msg_group_cutoff',
                        group=group.rec_name))

    @classmethod
    def delete(cls, groups):
        for group in groups:
            if group.reservations:
                raise GroupError(gettext(
                        'hotel_groups.msg_group_delete_reservations',
                        group=group.rec_name))
        super().delete(groups)

    @property
    def nights(self):
        return max((self.end_date - self.start_date).days, 1)

    def get_start(self):
        return datetime.datetime.combine(
            self.start_date, self.hotel.check_in_time)

    def get_end(self):
        return datetime.datetime.combine(
            self.end_date, self.hotel.check_out_time)

    def generate(self):
        """Create one draft reservation for each blocked room not yet
        generated and return them."""
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        vlist = []
        for block in self.blocks:
            missing = block.quantity - block.pickup
            for _ in range(max(missing, 0)):
                line = {
                    'room_type': block.room_type.id,
                    'pricing': 'nightly',
                    }
                if block.nightly_price is not None:
                    line.update({
                            'billed_as': 'nightly',
                            'quantity': self.nights,
                            'unit_price': round_price(block.nightly_price),
                            })
                vlist.append({
                        'company': self.company.id,
                        'hotel': self.hotel.id,
                        'party': self.party.id,
                        'group': self.id,
                        'start': self.get_start(),
                        'end': self.get_end(),
                        'notes': self.name,
                        'lines': [('create', [line])],
                        })
        return Reservation.create(vlist) if vlist else []

    @classmethod
    @ModelView.button
    def draft(cls, groups):
        cls.write(groups, {'state': 'draft'})

    @classmethod
    @ModelView.button
    def definite(cls, groups):
        for group in groups:
            if not group.blocks:
                raise GroupError(gettext(
                        'hotel_groups.msg_group_no_blocks',
                        group=group.rec_name))
        cls.write(groups, {'state': 'definite'})

    @classmethod
    @ModelView.button
    def cancel(cls, groups):
        Reservation = Pool().get('hotel.reservation')
        reservations = [
            r for g in groups for r in g.reservations
            if r.state in {'draft', 'confirmed'}]
        if reservations:
            Reservation.cancel(reservations)
        cls.write(groups, {'state': 'cancelled'})

    @classmethod
    @ModelView.button
    def generate_reservations(cls, groups):
        for group in groups:
            group.generate()

    @classmethod
    @ModelView.button
    def release(cls, groups):
        Block = Pool().get('hotel.group.block')
        today = Pool().get('ir.date').today()
        for group in groups:
            if not group.cutoff_date or today <= group.cutoff_date:
                raise GroupError(gettext(
                        'hotel_groups.msg_group_cutoff_not_passed',
                        group=group.rec_name))
            for block in group.blocks:
                pickup = block.pickup
                if pickup:
                    Block.write([block], {'quantity': pickup})
                else:
                    Block.delete([block])


class GroupBlock(ModelSQL, ModelView):
    "Hotel Group Room Block"
    __name__ = 'hotel.group.block'

    group = fields.Many2One(
        'hotel.group', "Group", required=True, ondelete='CASCADE')
    hotel = fields.Function(
        fields.Many2One('hotel.hotel', "Hotel"), 'on_change_with_hotel')
    room_type = fields.Many2One(
        'hotel.room.type', "Room Type", required=True,
        domain=[('hotel', '=', Eval('hotel', -1))])
    quantity = fields.Integer(
        "Rooms", required=True, domain=[('quantity', '>', 0)])
    nightly_price = fields.Numeric(
        "Nightly Price", digits=price_digits,
        domain=[If(Bool(Eval('nightly_price')),
                ('nightly_price', '>=', 0), ())],
        help="Negotiated price of a night. Empty uses the hotel rates "
        "when the reservation is confirmed.")
    pickup = fields.Function(
        fields.Integer("Picked Up"), 'get_pickup')
    remaining = fields.Function(
        fields.Integer("Remaining"), 'get_remaining')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('type_unique', Unique(t, t.group, t.room_type),
                'hotel_groups.msg_block_unique'),
            ]

    @fields.depends('group', '_parent_group.hotel')
    def on_change_with_hotel(self, name=None):
        if self.group and self.group.hotel:
            return self.group.hotel.id

    @classmethod
    def validate(cls, blocks):
        super().validate(blocks)
        for block in blocks:
            rooms = len([r for r in block.room_type.rooms if r.active])
            if block.quantity > rooms:
                raise GroupError(gettext(
                        'hotel_groups.msg_block_too_many',
                        quantity=block.quantity,
                        room_type=block.room_type.rec_name,
                        rooms=rooms))

    @classmethod
    def get_pickup(cls, blocks, name):
        Line = Pool().get('hotel.reservation.line')
        result = {}
        for block in blocks:
            result[block.id] = Line.search([
                    ('reservation.group', '=', block.group.id),
                    ('reservation.state', '!=', 'cancelled'),
                    ('room_type', '=', block.room_type.id),
                    ], count=True)
        return result

    @classmethod
    def get_remaining(cls, blocks, name):
        return {
            b.id: max(b.quantity - b.get_pickup([b], 'pickup')[b.id], 0)
            for b in blocks}


class EventSpace(DeactivableMixin, ModelSQL, ModelView):
    "Hotel Event Space"
    __name__ = 'hotel.event.space'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    capacity = fields.Integer(
        "Capacity", required=True, domain=[('capacity', '>', 0)])
    product = fields.Many2One(
        'product.product', "Hourly Product",
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        help="The service product invoiced for each hour of use.")
    hourly_price = fields.Numeric(
        "Hourly Price", digits=price_digits,
        domain=[If(Bool(Eval('hourly_price')),
                ('hourly_price', '>=', 0), ())])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_unique', Unique(t, t.hotel, t.name),
                'hotel_groups.msg_space_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_capacity(cls):
        return 10


class Event(ModelSQL, ModelView):
    "Hotel Event"
    __name__ = 'hotel.event'

    name = fields.Char("Name", required=True)
    space = fields.Many2One(
        'hotel.event.space', "Space", required=True,
        states={'readonly': Eval('state') == 'cancelled'})
    group = fields.Many2One(
        'hotel.group', "Group", ondelete='SET NULL')
    start = fields.DateTime("Start", required=True)
    end = fields.DateTime(
        "End", required=True,
        domain=[If(Bool(Eval('start')), ('end', '>', Eval('start')), ())])
    date = fields.Function(fields.Date("Date"), 'get_date')
    attendees = fields.Integer(
        "Attendees", required=True, domain=[('attendees', '>=', 0)])
    reservation = fields.Many2One(
        'hotel.reservation', "Billed On",
        domain=[('group', '=', Eval('group', -1))],
        states={'readonly': Bool(Eval('charge'))},
        help="The reservation of the group on which the space is charged.")
    charge = fields.Many2One(
        'hotel.reservation.charge', "Charge", readonly=True)
    notes = fields.Text("Notes")
    state = fields.Selection([
            ('draft', "Draft"),
            ('confirmed', "Confirmed"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'confirmed', 'success',
                        If(Eval('state') == 'cancelled', 'muted',
                        '')))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('start', 'DESC'))
        cls._buttons.update({
                'draft': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                'confirm': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': Eval('state') == 'cancelled',
                    'depends': ['state'],
                    },
                'bill': {
                    'invisible': (
                        (Eval('state') != 'confirmed')
                        | Bool(Eval('charge'))),
                    'depends': ['state', 'charge'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def default_attendees(cls):
        return 0

    def get_date(self, name):
        return self.start.date() if self.start else None

    @classmethod
    def validate(cls, events):
        super().validate(events)
        for event in events:
            event.check_capacity()
            event.check_overlap()

    def check_capacity(self):
        if self.state == 'cancelled':
            return
        if self.attendees > self.space.capacity:
            raise EventError(gettext(
                    'hotel_groups.msg_event_capacity',
                    event=self.rec_name, space=self.space.rec_name,
                    capacity=self.space.capacity))

    def check_overlap(self):
        if self.state == 'cancelled':
            return
        others = self.search([
                ('space', '=', self.space.id),
                ('state', '!=', 'cancelled'),
                ('start', '<', self.end),
                ('end', '>', self.start),
                ('id', '!=', self.id),
                ], limit=1)
        if others:
            raise EventError(gettext(
                    'hotel_groups.msg_event_overlap',
                    event=self.rec_name, space=self.space.rec_name,
                    other=others[0].rec_name))

    @classmethod
    @ModelView.button
    def draft(cls, events):
        cls.write(events, {'state': 'draft'})

    @classmethod
    @ModelView.button
    def confirm(cls, events):
        cls.write(events, {'state': 'confirmed'})

    @classmethod
    @ModelView.button
    def cancel(cls, events):
        cls.write(events, {'state': 'cancelled'})

    @classmethod
    @ModelView.button
    def bill(cls, events):
        Charge = Pool().get('hotel.reservation.charge')
        for event in events:
            space = event.space
            if not event.reservation:
                raise EventError(gettext(
                        'hotel_groups.msg_event_no_reservation',
                        event=event.rec_name))
            if space.product is None or space.hourly_price is None:
                raise EventError(gettext(
                        'hotel_groups.msg_event_no_price',
                        space=space.rec_name))
            seconds = (event.end - event.start).total_seconds()
            hours = -(-seconds // 3600)
            charge = Charge()
            charge.reservation = event.reservation
            charge.product = space.product
            charge.description = "%s - %s" % (event.name, space.name)
            charge.quantity = float(hours)
            charge.unit = space.product.sale_uom
            charge.unit_price = round_price(space.hourly_price)
            charge.save()
            cls.write([event], {'charge': charge.id})


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    group = fields.Many2One(
        'hotel.group', "Group", ondelete='SET NULL',
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': Eval('state') != 'draft'})

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('group', None)
        return super().copy(reservations, default=default)


class ReservationLine(metaclass=PoolMeta):
    __name__ = 'hotel.reservation.line'

    guest = fields.Function(
        fields.Many2One('party.party', "Guest"),
        'get_guest', searcher='search_guest')

    def get_guest(self, name):
        return self.reservation.party.id

    @classmethod
    def search_guest(cls, name, clause):
        return [('reservation.party',) + tuple(clause[1:])]
