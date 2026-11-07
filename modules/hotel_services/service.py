# This file is part of the Tryton hotel_services module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, Workflow, fields)
from trytond.modules.product import price_digits
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import ServiceError

CATEGORIES = [
    ('spa', "Spa & Wellness"),
    ('activity', "Activity"),
    ('transport', "Transport"),
    ('other', "Other"),
    ]


def _times(date, start_time, minutes):
    "Return the (start, end) datetimes of a slot"
    start = datetime.datetime.combine(date, start_time)
    return start, start + datetime.timedelta(minutes=minutes)


class HotelCompanyMixin:
    "The company of the records is the company of their hotel"
    __slots__ = ()

    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')

    @fields.depends('hotel', '_parent_hotel.company')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]


class Resource(HotelCompanyMixin, DeactivableMixin, ModelSQL, ModelView):
    "Service Resource"
    __name__ = 'hotel.service.resource'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True)
    notes = fields.Char("Notes")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_unique', Unique(t, t.hotel, t.name),
                'hotel_services.msg_resource_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))


class Service(HotelCompanyMixin, DeactivableMixin, ModelSQL, ModelView):
    "Hotel Service"
    __name__ = 'hotel.service'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    category = fields.Selection(CATEGORIES, "Category", required=True)
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        context={'company': Eval('company', -1)},
        help="The service invoiced to the guest.")
    duration = fields.Integer(
        "Duration (min)", required=True, domain=[('duration', '>', 0)])
    capacity = fields.Integer(
        "Capacity", required=True, domain=[('capacity', '>=', 1)],
        help="Number of participants at the same time.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_category(cls):
        return 'spa'

    @classmethod
    def default_duration(cls):
        return 60

    @classmethod
    def default_capacity(cls):
        return 1


class Booking(HotelCompanyMixin, Workflow, ModelSQL, ModelView):
    "Service Booking"
    __name__ = 'hotel.service.booking'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        states={'readonly': Eval('state') != 'draft'})
    service = fields.Many2One(
        'hotel.service', "Service", required=True,
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': Eval('state') != 'draft'})
    resource = fields.Many2One(
        'hotel.service.resource', "Resource",
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': Eval('state') != 'draft'},
        help="Therapist, room or vehicle.")
    party = fields.Many2One(
        'party.party', "Guest", required=True,
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'draft'})
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation",
        domain=[('hotel', '=', Eval('hotel', -1)),
            ('state', 'in', ['confirmed', 'checked_in', 'checked_out'])],
        states={'readonly': Eval('state') != 'draft'},
        help="The price is added to the charges of the reservation. "
        "Without reservation a sale is created.")
    date = fields.Date(
        "Date", required=True, states={'readonly': Eval('state') != 'draft'})
    start_time = fields.Time(
        "Start", required=True,
        states={'readonly': Eval('state') != 'draft'})
    duration = fields.Integer(
        "Duration (min)", required=True, domain=[('duration', '>', 0)],
        states={'readonly': Eval('state') != 'draft'})
    end_time = fields.Function(fields.Time("End"), 'on_change_with_end_time')
    participants = fields.Integer(
        "Participants", required=True, domain=[('participants', '>=', 1)],
        states={'readonly': Eval('state') != 'draft'})
    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits, required=True,
        states={'readonly': Eval('state') != 'draft'})
    amount = fields.Function(
        fields.Numeric("Amount", digits=(16, 2)), 'on_change_with_amount')
    charge = fields.Many2One(
        'hotel.reservation.charge', "Charge", readonly=True,
        ondelete='SET NULL')
    sale = fields.Many2One(
        'sale.sale', "Sale", readonly=True, ondelete='SET NULL')
    notes = fields.Text("Notes")
    state = fields.Selection([
            ('draft', "Draft"),
            ('confirmed', "Confirmed"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'confirmed', 'success',
                        If(Eval('state') == 'done', 'muted',
                        If(Eval('state') == 'cancelled', 'muted',
                        ''))))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('start_time', 'ASC'))
        cls._transitions |= {
            ('draft', 'confirmed'),
            ('confirmed', 'draft'),
            ('confirmed', 'done'),
            ('draft', 'cancelled'),
            ('confirmed', 'cancelled'),
            ('cancelled', 'draft'),
            }
        cls._buttons.update({
                'draft': {
                    'invisible': ~Eval('state').in_(
                        ['confirmed', 'cancelled']),
                    'depends': ['state'],
                    },
                'confirm': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'complete': {
                    'invisible': Eval('state') != 'confirmed',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'confirmed']),
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_participants(cls):
        return 1

    @classmethod
    def default_state(cls):
        return 'draft'

    @fields.depends('service', 'duration', 'unit_price')
    def on_change_service(self):
        if self.service:
            self.duration = self.service.duration
            self.unit_price = self.service.product.list_price

    @fields.depends('start_time', 'duration')
    def on_change_with_end_time(self, name=None):
        if self.start_time and self.duration:
            start = datetime.datetime.combine(
                datetime.date.today(), self.start_time)
            return (start + datetime.timedelta(
                    minutes=self.duration)).time()

    @fields.depends('participants', 'unit_price')
    def on_change_with_amount(self, name=None):
        if self.participants and self.unit_price is not None:
            return (Decimal(self.participants) * self.unit_price).quantize(
                Decimal('0.01'))
        return Decimal(0)

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id(
                'hotel_services', 'sequence_booking'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, bookings, default=None):
        default = {} if default is None else default.copy()
        for name in ['number', 'charge', 'sale']:
            default.setdefault(name, None)
        return super().copy(bookings, default=default)

    @classmethod
    def delete(cls, bookings):
        for booking in bookings:
            if booking.state == 'done':
                raise ServiceError(gettext(
                        'hotel_services.msg_booking_delete_done',
                        booking=booking.rec_name))
        super().delete(bookings)

    def _overlapping(self):
        "Active bookings of the same day overlapping this one"
        start, end = _times(self.date, self.start_time, self.duration)
        others = self.search([
                ('hotel', '=', self.hotel.id), ('date', '=', self.date),
                ('state', 'in', ['confirmed', 'done']),
                ('id', '!=', self.id)])
        result = []
        for other in others:
            o_start, o_end = _times(
                other.date, other.start_time, other.duration)
            if o_start < end and o_end > start:
                result.append(other)
        return result

    def check_availability(self):
        overlapping = self._overlapping()
        if self.resource:
            for other in overlapping:
                if other.resource == self.resource:
                    raise ServiceError(gettext(
                            'hotel_services.msg_booking_resource_busy',
                            booking=self.rec_name,
                            resource=self.resource.rec_name,
                            other=other.rec_name))
        taken = sum(
            o.participants for o in overlapping if o.service == self.service)
        if taken + self.participants > self.service.capacity:
            raise ServiceError(gettext(
                    'hotel_services.msg_booking_capacity',
                    booking=self.rec_name, service=self.service.rec_name,
                    capacity=self.service.capacity))

    def _bill(self):
        pool = Pool()
        product = self.service.product
        description = "%s - %s" % (self.service.name, self.date)
        if self.reservation:
            Charge = pool.get('hotel.reservation.charge')
            charge = Charge()
            charge.reservation = self.reservation
            charge.product = product
            charge.description = description
            charge.quantity = float(self.participants)
            charge.unit = product.sale_uom
            charge.unit_price = self.unit_price
            charge.save()
            self.charge = charge
            return
        Sale = pool.get('sale.sale')
        SaleLine = pool.get('sale.line')
        sale = Sale()
        sale.company = self.company
        sale.currency = self.company.currency
        sale.party = self.party
        sale.sale_date = pool.get('ir.date').today()
        sale.description = self.rec_name
        sale.on_change_party()
        line = SaleLine()
        line.sale = sale
        line.product = product
        line.on_change_product()
        line.quantity = float(self.participants)
        line.unit_price = self.unit_price
        line.description = description
        sale.lines = [line]
        sale.save()
        self.sale = sale

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, bookings):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, bookings):
        for booking in bookings:
            booking.check_availability()

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def complete(cls, bookings):
        for booking in bookings:
            booking._bill()
        cls.save(bookings)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, bookings):
        pass


class Table(HotelCompanyMixin, DeactivableMixin, ModelSQL, ModelView):
    "Restaurant Table"
    __name__ = 'hotel.restaurant.table'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True)
    area = fields.Char("Area", help="Terrace, main room, bar...")
    seats = fields.Integer(
        "Seats", required=True, domain=[('seats', '>=', 1)])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_unique', Unique(t, t.hotel, t.name),
                'hotel_services.msg_table_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_seats(cls):
        return 2


class TableBooking(HotelCompanyMixin, ModelSQL, ModelView):
    "Restaurant Booking"
    __name__ = 'hotel.restaurant.booking'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    table = fields.Many2One(
        'hotel.restaurant.table', "Table", required=True,
        domain=[('hotel', '=', Eval('hotel', -1))])
    date = fields.Date("Date", required=True)
    time = fields.Time("Time", required=True)
    duration = fields.Integer(
        "Duration (min)", required=True, domain=[('duration', '>', 0)])
    covers = fields.Integer(
        "Covers", required=True, domain=[('covers', '>=', 1)])
    party = fields.Many2One(
        'party.party', "Guest",
        context={'company': Eval('company', -1)})
    guest_name = fields.Char(
        "Name", states={'required': ~Bool(Eval('party'))})
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation",
        domain=[('hotel', '=', Eval('hotel', -1))],
        help="Stay of the guest, for the room charge at the restaurant.")
    state = fields.Selection([
            ('booked', "Booked"),
            ('seated', "Seated"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
            ('no_show', "No-Show"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'seated', 'success',
                        If(Eval('state') == 'done', 'muted',
                        If(Eval('state') == 'cancelled', 'muted',
                        If(Eval('state') == 'no_show', 'danger',
                        ''))))),
            ]
    notes = fields.Text("Notes")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('time', 'ASC'))
        cls._buttons.update({
                'seat': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state'],
                    },
                'finish': {
                    'invisible': Eval('state') != 'seated',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state'],
                    },
                'no_show': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_duration(cls):
        return 90

    @classmethod
    def default_covers(cls):
        return 2

    @classmethod
    def default_state(cls):
        return 'booked'

    @classmethod
    def validate(cls, bookings):
        super().validate(bookings)
        for booking in bookings:
            booking.check_booking()

    def check_booking(self):
        if self.covers > self.table.seats:
            raise ServiceError(gettext(
                    'hotel_services.msg_table_seats',
                    table=self.table.rec_name, seats=self.table.seats,
                    covers=self.covers))
        if self.state not in {'booked', 'seated'}:
            return
        start, end = _times(self.date, self.time, self.duration)
        for other in self.search([
                    ('table', '=', self.table.id),
                    ('date', '=', self.date),
                    ('state', 'in', ['booked', 'seated']),
                    ('id', '!=', self.id)]):
            o_start, o_end = _times(other.date, other.time, other.duration)
            if o_start < end and o_end > start:
                raise ServiceError(gettext(
                        'hotel_services.msg_table_busy',
                        table=self.table.rec_name,
                        time=other.time.strftime('%H:%M')))

    def get_rec_name(self, name):
        return "%s %s %s" % (
            self.table.rec_name, self.date, self.time.strftime('%H:%M'))

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('table.name',) + tuple(clause[1:])]

    @classmethod
    @ModelView.button
    def seat(cls, bookings):
        cls.write(bookings, {'state': 'seated'})

    @classmethod
    @ModelView.button
    def finish(cls, bookings):
        cls.write(bookings, {'state': 'done'})

    @classmethod
    @ModelView.button
    def cancel(cls, bookings):
        cls.write(bookings, {'state': 'cancelled'})

    @classmethod
    @ModelView.button
    def no_show(cls, bookings):
        cls.write(bookings, {'state': 'no_show'})
