# This file is part of the Tryton restaurant module.
import datetime

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import ReservationError


class Reservation(Workflow, ModelSQL, ModelView):
    "Table Reservation"
    __name__ = 'restaurant.reservation'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'booked'},
        help="Company that owns the record.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'booked'},
        help="Restaurant concerned.")
    party = fields.Many2One(
        'party.party', "Customer",
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'booked'},
        help="Customer or contact concerned.")
    guest_name = fields.Char(
        "Guest Name",
        states={'required': ~Bool(Eval('party')),
            'readonly': Eval('state') != 'booked'},
        help="Name under which the table is booked.")
    phone = fields.Char(
        "Phone", states={'readonly': Eval('state') != 'booked'},
        help="Phone number to call for this record.")
    date = fields.Date(
        "Date", required=True, states={'readonly': Eval('state') != 'booked'},
        help="Date of the document.")
    time = fields.Time(
        "Time", required=True, states={'readonly': Eval('state') != 'booked'},
        help="Time of the day.")
    duration = fields.Integer(
        "Duration (min)", required=True,
        domain=[('duration', '>', 0)],
        states={'readonly': Eval('state') != 'booked'},
        help="Expected length of the booking, in minutes.")
    persons = fields.Integer(
        "Persons", required=True, domain=[('persons', '>', 0)],
        states={'readonly': Eval('state') != 'booked'},
        help="Number of guests expected.")
    table = fields.Many2One(
        'restaurant.table', "Table",
        domain=[('restaurant', '=', Eval('restaurant', -1))],
        states={'readonly': Eval('state') != 'booked'},
        help="Table of the restaurant.")
    order = fields.Many2One('restaurant.order', "Order", readonly=True,
        help="Order concerned.")
    notes = fields.Text("Notes",
        help="Free text for internal use.")
    state = fields.Selection([
            ('booked', "Booked"),
            ('seated', "Seated"),
            ('cancelled', "Cancelled"),
            ('no_show', "No Show"),
            ], "State", readonly=True, required=True,
        help="Current step of the workflow.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('time', 'DESC'))
        cls._transitions |= {
            ('booked', 'seated'), ('booked', 'cancelled'),
            ('booked', 'no_show'), ('cancelled', 'booked'),
            }
        cls._buttons.update({
                'seat': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state']},
                'cancel': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state']},
                'no_show_': {
                    'invisible': Eval('state') != 'booked',
                    'depends': ['state']},
                'rebook': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state').in_(['cancelled']), 'muted',
                    If(Eval('state') == 'no_show', 'danger',
                        If(Eval('state') == 'seated', 'success', '')))),
            ]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_restaurant(cls):
        return Pool().get('restaurant.order').default_restaurant()

    @classmethod
    def default_state(cls):
        return 'booked'

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_time(cls):
        return datetime.time(19, 0)

    @classmethod
    def default_duration(cls):
        return 90

    @classmethod
    def default_persons(cls):
        return 2

    @fields.depends('party', 'guest_name', 'phone')
    def on_change_party(self):
        if self.party:
            self.guest_name = self.party.rec_name
            if self.party.phone:
                self.phone = self.party.phone

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id(
                'restaurant', 'sequence_reservation'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        default.setdefault('order', None)
        default.setdefault('state', 'booked')
        return super().copy(reservations, default=default)

    @classmethod
    def delete(cls, reservations):
        for r in reservations:
            if r.state == 'seated':
                raise ReservationError(gettext(
                        'restaurant.msg_reservation_delete',
                        reservation=r.rec_name))
        super().delete(reservations)

    def _span(self):
        start = datetime.datetime.combine(self.date, self.time)
        return start, start + datetime.timedelta(minutes=self.duration)

    @classmethod
    def validate(cls, reservations):
        super().validate(reservations)
        for r in reservations:
            if r.state != 'booked' or not r.table:
                continue
            if r.persons > r.table.seats:
                raise ReservationError(gettext(
                        'restaurant.msg_reservation_seats',
                        table=r.table.rec_name, seats=r.table.seats))
            start, end = r._span()
            others = cls.search([
                    ('table', '=', r.table.id), ('date', '=', r.date),
                    ('state', '=', 'booked'), ('id', '!=', r.id)])
            for other in others:
                o_start, o_end = other._span()
                if start < o_end and o_start < end:
                    raise ReservationError(gettext(
                            'restaurant.msg_reservation_overlap',
                            table=r.table.rec_name,
                            reservation=other.rec_name))

    @classmethod
    @ModelView.button
    @Workflow.transition('seated')
    def seat(cls, reservations):
        "Seat the guests: opens the order of the table"
        Order = Pool().get('restaurant.order')
        for r in reservations:
            if not r.table:
                raise ReservationError(gettext(
                        'restaurant.msg_reservation_no_table',
                        reservation=r.rec_name))
            order, = Order.create([{
                        'company': r.company.id,
                        'restaurant': r.restaurant.id, 'type': 'dine_in',
                        'table': r.table.id,
                        'party': r.party.id if r.party else None,
                        'covers': r.persons,
                        'tab_name': None, 'date': r.date}])
            super(Reservation, cls).write([r], {'order': order.id})

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, reservations):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('no_show')
    def no_show_(cls, reservations):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('booked')
    def rebook(cls, reservations):
        pass
