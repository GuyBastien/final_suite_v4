# This file is part of the Tryton hotel_booking_engine module.
"""Online booking: settings, search and reservation of the public pages.

Online payment is not included: it needs a payment provider. The hotel can
however request a deposit (``booking_deposit_rate``): the amount is stored
in ``Reservation.deposit_requested`` and displayed on the confirmation, so
that a payment module or the front desk can collect it.
"""
import datetime
import re
import secrets
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, Unique, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from trytond.modules.hotel.exceptions import RateError

from .exceptions import BookingError, BookingUnavailableError

SOURCES = [
    ('direct', "Direct"),
    ('web', "Web"),
    ('phone', "Phone"),
    ('channel', "Channel"),
    ('other', "Other"),
    ]
MAX_LENGTHS = {
    'first_name': 60, 'last_name': 60, 'email': 120, 'phone': 40,
    'notes': 500,
    }
MAX_ADVANCE_DAYS = 730
_EMAIL = re.compile(r'^[^@\s<>"]{1,64}@[^@\s<>"]{1,255}\.[^@\s<>"]{2,}$')
_STATES = {'invisible': ~Eval('booking_engine_enabled', False)}


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    booking_engine_enabled = fields.Boolean(
        "Online Booking",
        help="Publish the booking page and the availability API of the "
        "hotel.")
    booking_token = fields.Char(
        "Booking Token", readonly=True, states=_STATES,
        help="Secret of the booking address.")
    booking_url = fields.Function(
        fields.Char(
            "Booking Address", states=_STATES,
            help="Give this address (prefixed by the server URL) to the "
            "guests: it is the public booking page."),
        'get_booking_url')
    booking_auto_confirm = fields.Boolean(
        "Confirm Automatically", states=_STATES,
        help="Assign the rooms and confirm the online reservations at once. "
        "Otherwise they stay as drafts for the front desk.")
    booking_max_nights = fields.Integer(
        "Maximum Nights", states={
            'invisible': ~Eval('booking_engine_enabled', False),
            'required': Bool(Eval('booking_engine_enabled')),
            }, domain=[('booking_max_nights', '>=', 1)])
    booking_max_rooms = fields.Integer(
        "Maximum Rooms", states={
            'invisible': ~Eval('booking_engine_enabled', False),
            'required': Bool(Eval('booking_engine_enabled')),
            }, domain=[('booking_max_rooms', '>=', 1)])
    booking_lead_hours = fields.Integer(
        "Lead Time (hours)", states={
            'invisible': ~Eval('booking_engine_enabled', False),
            'required': Bool(Eval('booking_engine_enabled')),
            }, domain=[('booking_lead_hours', '>=', 0)],
        help="Minimum delay between the booking and the arrival.")
    booking_deposit_rate = fields.Numeric(
        "Deposit Rate", digits=(16, 4), states=_STATES,
        domain=[If(Bool(Eval('booking_deposit_rate')), [
                    ('booking_deposit_rate', '>=', 0),
                    ('booking_deposit_rate', '<=', 1)], [])],
        help="Share of the total requested as a deposit "
        "(0.30 for 30%). Payment itself is not handled online.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('booking_token_unique', Unique(t, t.booking_token),
                'hotel_booking_engine.msg_booking_token_unique'),
            ]
        cls._buttons.update({
                'new_booking_token': {
                    'invisible': ~Eval('booking_engine_enabled', False),
                    'depends': ['booking_engine_enabled'],
                    },
                })

    @classmethod
    def default_booking_engine_enabled(cls):
        return False

    @classmethod
    def default_booking_auto_confirm(cls):
        return False

    @classmethod
    def default_booking_max_nights(cls):
        return 30

    @classmethod
    def default_booking_max_rooms(cls):
        return 3

    @classmethod
    def default_booking_lead_hours(cls):
        return 0

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if values.get('booking_engine_enabled'):
                values.setdefault('booking_token', secrets.token_urlsafe(24))
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        args = []
        for hotels, values in zip(actions, actions):
            args.extend((hotels, values))
            if values.get('booking_engine_enabled'):
                for hotel in hotels:
                    if not hotel.booking_token:
                        args.extend(([hotel], {
                                    'booking_token':
                                    secrets.token_urlsafe(24)}))
        super().write(*args)

    def get_booking_url(self, name):
        if self.id is None or self.id < 0 or not self.booking_token:
            return None
        return '/%s/hotel/booking/%s/' % (
            Transaction().database.name, self.booking_token)

    @classmethod
    @ModelView.button
    def new_booking_token(cls, hotels):
        for hotel in hotels:
            cls.write([hotel], {'booking_token': secrets.token_urlsafe(24)})

    @classmethod
    def booking_by_token(cls, token):
        "Return the hotel published with the token (or None)"
        if not token or len(token) > 100:
            return None
        hotels = cls.search([
                ('booking_token', '=', token),
                ('booking_engine_enabled', '=', True),
                ], limit=1)
        return hotels[0] if hotels else None

    # Search

    def booking_period(self, arrival, departure, adults, children, rooms):
        "Validate the request and return (start, end) datetimes"
        today = datetime.date.today()
        if (not isinstance(arrival, datetime.date)
                or not isinstance(departure, datetime.date)
                or departure <= arrival):
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_dates_invalid'))
        nights = (departure - arrival).days
        if nights > self.booking_max_nights:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_too_long',
                    nights=self.booking_max_nights))
        if arrival > today + datetime.timedelta(days=MAX_ADVANCE_DAYS):
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_too_far'))
        start = datetime.datetime.combine(arrival, self.check_in_time)
        end = datetime.datetime.combine(departure, self.check_out_time)
        earliest = datetime.datetime.now() + datetime.timedelta(
            hours=self.booking_lead_hours)
        if start < earliest and arrival < today + datetime.timedelta(days=1):
            # Same-day arrival is allowed until the lead time before the
            # end of the arrival day
            limit = datetime.datetime.combine(arrival, datetime.time.max)
            if limit < earliest:
                raise BookingError(gettext(
                        'hotel_booking_engine.msg_booking_in_past',
                        hours=self.booking_lead_hours))
        elif start < earliest and arrival < today:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_in_past',
                    hours=self.booking_lead_hours))
        if (not isinstance(adults, int) or not isinstance(children, int)
                or not isinstance(rooms, int)
                or adults < 1 or children < 0 or adults + children > 50):
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_guests'))
        if rooms < 1 or rooms > self.booking_max_rooms:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_rooms',
                    rooms=self.booking_max_rooms))
        return start, end

    def booking_search(self, arrival, departure, adults=1, children=0,
            rooms=1):
        "Return the room types offered with their availability and price"
        start, end = self.booking_period(
            arrival, departure, adults, children, rooms)
        currency = self.company.currency
        result = []
        for room_type in self.room_types:
            if not room_type.active:
                continue
            if room_type.capacity * rooms < adults + children:
                continue
            available = len(room_type.get_available_rooms(start, end))
            if available < rooms:
                continue
            try:
                price = room_type.compute_price(start, end, mode='nightly')
            except RateError:
                continue
            result.append({
                    'id': room_type.id,
                    'code': room_type.code,
                    'name': room_type.name,
                    'description': room_type.description or '',
                    'capacity': room_type.capacity,
                    'available': available,
                    'nights': int(price['quantity']),
                    'night_price': price['unit_price'],
                    'total': currency.round(price['amount'] * rooms),
                    'currency': currency.code,
                    })
        return result

    # Reservation

    @classmethod
    def _clean(cls, data, name, required=False):
        value = data.get(name)
        if value is None:
            value = ''
        if not isinstance(value, str):
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_field_invalid',
                    field=name))
        value = value.strip()
        if len(value) > MAX_LENGTHS[name]:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_field_too_long',
                    field=name))
        if required and not value:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_field_required',
                    field=name))
        return value

    def booking_guest(self, data):
        "Find (by e-mail) or create the party of the guest"
        pool = Pool()
        Party = pool.get('party.party')
        Mechanism = pool.get('party.contact_mechanism')
        first_name = self._clean(data, 'first_name')
        last_name = self._clean(data, 'last_name', required=True)
        email = self._clean(data, 'email', required=True).lower()
        phone = self._clean(data, 'phone')
        if not _EMAIL.match(email):
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_email_invalid'))
        mechanisms = Mechanism.search([
                ('type', '=', 'email'), ('value', '=', email),
                ('party.active', '=', True)], limit=1)
        if mechanisms:
            return mechanisms[0].party
        party, = Party.create([{
                    'name': last_name, 'first_name': first_name or None,
                    'is_person': True,
                    'contact_mechanisms': [('create', [{
                                    'type': 'email', 'value': email}]
                            + ([{'type': 'phone', 'value': phone}]
                                if phone else []))],
                    }])
        return party

    def booking_reserve(self, arrival, departure, adults, children, rooms,
            guest, notes=''):
        """Create the reservation requested online.

        rooms is a dict {room type id: quantity}. Returns the reservation.
        """
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        Notification = pool.get('hotel.notification')
        if not isinstance(rooms, dict) or not rooms:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_rooms',
                    rooms=self.booking_max_rooms))
        quantities = {}
        for key, quantity in rooms.items():
            try:
                key, quantity = int(key), int(quantity)
            except (TypeError, ValueError):
                raise BookingError(gettext(
                        'hotel_booking_engine.msg_booking_room_type'))
            if quantity < 0:
                raise BookingError(gettext(
                        'hotel_booking_engine.msg_booking_room_type'))
            if quantity:
                quantities[key] = quantity
        total_rooms = sum(quantities.values())
        start, end = self.booking_period(
            arrival, departure, adults, children, total_rooms or 1)
        known = {rt.id: rt for rt in self.room_types if rt.active}
        capacity = 0
        for key, quantity in quantities.items():
            if key not in known:
                raise BookingError(gettext(
                        'hotel_booking_engine.msg_booking_room_type'))
            capacity += known[key].capacity * quantity
        if capacity < adults + children:
            raise BookingError(gettext(
                    'hotel_booking_engine.msg_booking_capacity'))
        notes = self._clean({'notes': notes}, 'notes')
        phone = self._clean(guest, 'phone')

        with Transaction().set_context(company=self.company.id):
            party = self.booking_guest(guest)
            lines, used = [], set()
            for key, quantity in sorted(quantities.items()):
                room_type = known[key]
                free = [r for r in room_type.get_available_rooms(start, end)
                    if r.id not in used]
                if len(free) < quantity:
                    raise BookingUnavailableError(gettext(
                            'hotel_booking_engine.msg_booking_unavailable',
                            room_type=room_type.rec_name))
                for room in free[:quantity]:
                    used.add(room.id)
                    line = {'room_type': room_type.id}
                    if self.booking_auto_confirm:
                        line['room'] = room.id
                    lines.append(line)
            comment = [
                gettext('hotel_booking_engine.msg_booking_note_source')]
            if phone:
                comment.append(phone)
            if notes:
                comment.append(notes)
            reservation, = Reservation.create([{
                        'company': self.company.id,
                        'hotel': self.id,
                        'party': party.id,
                        'start': start, 'end': end,
                        'adults': adults, 'children': children,
                        'source': 'web',
                        'notes': '\n'.join(comment),
                        'lines': [('create', lines)],
                        }])
            Reservation.compute([reservation])
            if self.booking_auto_confirm:
                Reservation.confirm([reservation])
            reservation = Reservation(reservation.id)
            if self.booking_deposit_rate:
                currency = self.company.currency
                Reservation.write([reservation], {
                        'deposit_requested': currency.round(
                            reservation.total_amount
                            * self.booking_deposit_rate)})
                reservation = Reservation(reservation.id)
            Notification.create([{
                        'hotel': self.id,
                        'kind': 'info',
                        'title': gettext(
                            'hotel_booking_engine.msg_booking_notification',
                            number=reservation.number),
                        'message': gettext(
                            'hotel_booking_engine.msg_booking_notification_text',
                            party=party.rec_name, start=start, end=end,
                            state=reservation.state),
                        'origin': str(reservation),
                        }])
        return reservation


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    source = fields.Selection(
        SOURCES, "Source", required=True,
        help="How the reservation was received.")
    deposit_requested = fields.Numeric(
        "Deposit Requested", digits=(16, Eval('currency_digits', 2)),
        readonly=True,
        help="Deposit asked to the guest by the online booking. Collecting "
        "it is not handled online.")

    @classmethod
    def default_source(cls):
        return 'direct'

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('deposit_requested', None)
        return super().copy(reservations, default=default)
