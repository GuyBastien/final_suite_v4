# This file is part of the Tryton hotel_channel_manager module.
import datetime
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

from . import ical
from .exceptions import ChannelError

logger = logging.getLogger(__name__)

PROVIDERS = [
    ('ical', "iCal Calendar"),
    ('booking', "Booking.com"),
    ('expedia', "Expedia"),
    ('airbnb', "Airbnb"),
    ('other', "Other"),
    ]
MAX_SIZE = 5 * 1024 * 1024
# Reservations which still block the room
ACTIVE = ['confirmed', 'checked_in']


class Channel(DeactivableMixin, ModelSQL, ModelView):
    "Hotel Distribution Channel"
    __name__ = 'hotel.channel'

    name = fields.Char("Name", required=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True, ondelete='CASCADE')
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    provider = fields.Selection(PROVIDERS, "Provider", required=True)
    party = fields.Many2One(
        'party.party', "Party", required=True,
        context={'company': Eval('company', -1)},
        help="The party of the reservations coming from the channel "
        "(usually the OTA itself).")
    compute_prices = fields.Boolean(
        "Compute Prices",
        help="Price the imported reservations with the hotel rates. "
        "Otherwise they are created at price 0 (the channel collects "
        "the payment).")
    mappings = fields.One2Many(
        'hotel.channel.mapping', 'channel', "Rooms")
    bookings = fields.One2Many(
        'hotel.channel.booking', 'channel', "Bookings", readonly=True)
    logs = fields.One2Many(
        'hotel.channel.log', 'channel', "Logs", readonly=True)
    last_synchronization = fields.DateTime(
        "Last Synchronization", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('name', 'ASC'))
        cls._buttons.update({
                'synchronize': {},
                })

    @classmethod
    def default_provider(cls):
        return 'ical'

    @classmethod
    def default_compute_prices(cls):
        return True

    @fields.depends('hotel')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]

    @classmethod
    @ModelView.button
    def synchronize(cls, channels):
        "Import the calendars of the channels"
        Log = Pool().get('hotel.channel.log')
        logs = []
        for channel in channels:
            logs.extend(channel._synchronize())
        Log.save(logs)
        cls.write(channels, {'last_synchronization': datetime.datetime.now()})

    @classmethod
    def synchronize_all(cls):
        "Cron entry point"
        cls.synchronize(cls.search([]))

    def _synchronize(self):
        Log = Pool().get('hotel.channel.log')
        logs = []
        for mapping in self.mappings:
            if not mapping.import_url:
                continue
            log = Log(
                channel=self, mapping=mapping, direction='import',
                date=datetime.datetime.now())
            try:
                counts = mapping.synchronize()
            except (ChannelError, ValueError, OSError) as exception:
                log.state = 'error'
                log.message = getattr(exception, 'message', None) or str(exception)
                logger.warning(
                    "Channel %s, room %s: %s", self.rec_name,
                    mapping.room.rec_name, exception)
            else:
                log.state = 'success'
                log.created = counts['created']
                log.updated = counts['updated']
                log.cancelled = counts['cancelled']
                log.conflicts = counts['conflicts']
            logs.append(log)
        return logs


class Mapping(ModelSQL, ModelView):
    "Hotel Channel Room"
    __name__ = 'hotel.channel.mapping'
    _rec_name = 'name'

    name = fields.Function(
        fields.Char("Name"), 'get_name', searcher='search_name')
    channel = fields.Many2One(
        'hotel.channel', "Channel", required=True, ondelete='CASCADE')
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    hotel = fields.Function(
        fields.Many2One('hotel.hotel', "Hotel"), 'on_change_with_hotel')
    room = fields.Many2One(
        'hotel.room', "Room", required=True, ondelete='CASCADE',
        domain=[('hotel', '=', Eval('hotel', -1))],
        depends=['hotel'])
    external_id = fields.Char(
        "External ID", help="The identifier of the room on the channel.")
    import_url = fields.Char(
        "Import URL",
        help="The iCal address of the room calendar published by the "
        "channel. Its reservations block the room.")
    token = fields.Char(
        "Token", readonly=True, required=True,
        help="Secret of the export address.")
    export_path = fields.Function(
        fields.Char(
            "Export Address",
            help="Give the channel this address (prefixed by the server "
            "URL): it publishes the occupancy of the room."),
        'get_export_path')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('channel_room_unique', Unique(t, t.channel, t.room),
                'hotel_channel_manager.msg_mapping_room_unique'),
            ('token_unique', Unique(t, t.token),
                'hotel_channel_manager.msg_mapping_token_unique'),
            ]
        cls._buttons.update({
                'new_token': {},
                })

    @classmethod
    def default_token(cls):
        return secrets.token_urlsafe(24)

    @fields.depends('channel', '_parent_channel.hotel')
    def on_change_with_hotel(self, name=None):
        if self.channel:
            return self.channel.hotel.id

    def get_name(self, name):
        return "%s / %s" % (self.channel.rec_name, self.room.rec_name)

    @classmethod
    def search_name(cls, name, clause):
        return ['OR', ('channel.name',) + tuple(clause[1:]),
            ('room.name',) + tuple(clause[1:])]

    def get_company(self, name):
        return self.channel.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('channel.hotel.company',) + tuple(clause[1:])]

    def get_export_path(self, name):
        if self.id is None or self.id < 0 or not self.token:
            return None
        return '/%s/hotel/ical/%s.ics' % (
            Transaction().database.name, self.token)

    @classmethod
    def validate(cls, mappings):
        super().validate(mappings)
        for mapping in mappings:
            if mapping.room.hotel != mapping.channel.hotel:
                raise ChannelError(gettext(
                        'hotel_channel_manager.msg_mapping_room_hotel',
                        room=mapping.room.rec_name))
            if mapping.import_url:
                mapping._check_url(mapping.import_url)

    @classmethod
    def _check_url(cls, url):
        if urllib.parse.urlparse(url).scheme not in {'http', 'https'}:
            raise ChannelError(gettext(
                    'hotel_channel_manager.msg_channel_url_scheme'))

    @classmethod
    @ModelView.button
    def new_token(cls, mappings):
        for mapping in mappings:
            cls.write([mapping], {'token': secrets.token_urlsafe(24)})

    # Export

    def export_events(self):
        "The reservations of the room as calendar events"
        Line = Pool().get('hotel.reservation.line')
        db = Transaction().database.name
        low = datetime.datetime.now() - datetime.timedelta(days=1)
        events = []
        for line in Line.search([
                    ('room', '=', self.room.id),
                    ('reservation.state', 'in', ACTIVE),
                    ('reservation.end', '>=', low),
                    ]):
            reservation = line.reservation
            # Do not send back to a channel its own reservations
            if reservation.channel == self.channel:
                continue
            events.append({
                    'uid': 'reservation-%s-%s@%s' % (
                        reservation.id, self.room.id, db),
                    'summary': 'Reserved',
                    'start': reservation.start.date(),
                    'end': max(reservation.end.date(),
                        reservation.start.date() + datetime.timedelta(1)),
                    })
        events.sort(key=lambda e: (e['start'], e['uid']))
        return events

    def export_ical(self):
        return ical.build(
            self.export_events(),
            name="%s %s" % (self.channel.hotel.rec_name, self.room.rec_name))

    @classmethod
    def ical_by_token(cls, token):
        "Return the calendar text of the room published with the token"
        mappings = cls.search([
                ('token', '=', token), ('channel.active', '=', True)],
            limit=1)
        if mappings:
            return mappings[0].export_ical()

    # Import

    @classmethod
    def _fetch(cls, url):
        cls._check_url(url)
        request = urllib.request.Request(
            url, headers={'User-Agent': 'Tryton hotel_channel_manager'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_SIZE + 1)
        except urllib.error.URLError as exception:
            raise ChannelError(gettext(
                    'hotel_channel_manager.msg_channel_fetch',
                    error=str(exception))) from exception
        if len(data) > MAX_SIZE:
            raise ChannelError(gettext(
                    'hotel_channel_manager.msg_channel_too_large'))
        return data.decode('utf-8', errors='replace')

    def _period(self, event):
        hotel = self.channel.hotel
        start, end = event['start'], event['end']
        if not isinstance(start, datetime.datetime):
            start = datetime.datetime.combine(start, hotel.check_in_time)
        if not isinstance(end, datetime.datetime):
            end = datetime.datetime.combine(end, hotel.check_out_time)
        return start, end

    def synchronize(self):
        "Import the calendar: returns the counts of what changed"
        Booking = Pool().get('hotel.channel.booking')
        text = self._fetch(self.import_url)
        events = ical.parse(text)
        counts = dict.fromkeys(
            ['created', 'updated', 'cancelled', 'conflicts'], 0)
        known = {b.uid: b for b in Booking.search([('mapping', '=', self.id)])}
        seen = set()
        for event in events:
            seen.add(event['uid'])
            start, end = self._period(event)
            booking = known.get(event['uid'])
            cancelled = event['status'] == 'CANCELLED'
            if booking is None:
                if cancelled:
                    continue
                booking = Booking(
                    channel=self.channel, mapping=self, uid=event['uid'])
                booking.summary = event['summary']
                booking.start, booking.end = start, end
                booking.accept()
                counts['created'] += 1
            elif cancelled:
                if booking.state != 'cancelled':
                    booking.release()
                    counts['cancelled'] += 1
            elif (booking.state == 'conflict'
                    or (booking.start, booking.end) != (start, end)
                    or booking.state == 'cancelled'):
                changed = (booking.start, booking.end) != (start, end)
                booking.release(save=False)
                booking.summary = event['summary']
                booking.start, booking.end = start, end
                booking.accept()
                if changed:
                    counts['updated'] += 1
            if booking.state == 'conflict':
                counts['conflicts'] += 1
        for uid, booking in known.items():
            if uid not in seen and booking.state != 'cancelled':
                booking.release()
                counts['cancelled'] += 1
        return counts


class Booking(ModelSQL, ModelView):
    "Hotel Channel Booking"
    __name__ = 'hotel.channel.booking'
    _rec_name = 'uid'

    channel = fields.Many2One(
        'hotel.channel', "Channel", required=True, ondelete='CASCADE',
        readonly=True)
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    mapping = fields.Many2One(
        'hotel.channel.mapping', "Room", required=True, ondelete='CASCADE',
        readonly=True)
    uid = fields.Char("Reference", required=True, readonly=True)
    summary = fields.Char("Summary", readonly=True)
    start = fields.DateTime("Start", required=True, readonly=True)
    end = fields.DateTime("End", required=True, readonly=True)
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", readonly=True,
        ondelete='SET NULL')
    state = fields.Selection([
            ('confirmed', "Confirmed"),
            ('conflict', "Conflict"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)
    message = fields.Text("Message", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('mapping_uid_unique', Unique(t, t.mapping, t.uid),
                'hotel_channel_manager.msg_booking_uid_unique'),
            ]
        cls._order = [('start', 'DESC'), ('id', 'DESC')]
        cls._buttons.update({
                'retry': {
                    'invisible': Eval('state') != 'conflict',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'confirmed'

    def get_company(self, name):
        return self.channel.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('channel.hotel.company',) + tuple(clause[1:])]

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'conflict', 'danger',
                    If(Eval('state') == 'cancelled', 'muted', ''))),
            ]

    def accept(self):
        """Create and confirm the reservation blocking the room.

        If the room is not free the booking is flagged as a conflict and
        a notification is raised."""
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        room = self.mapping.room
        hotel = self.channel.hotel
        self.message = None
        if self.end <= self.start:
            self.state = 'conflict'
            self.message = gettext(
                'hotel_channel_manager.msg_booking_period')
            self.save()
            return
        if not room.is_available(self.start, self.end):
            self.state = 'conflict'
            self.message = gettext(
                'hotel_channel_manager.msg_booking_unavailable',
                room=room.rec_name, start=self.start, end=self.end)
            self.save()
            return
        reservation = Reservation(
            company=hotel.company, hotel=hotel, party=self.channel.party,
            start=self.start, end=self.end, channel=self.channel,
            external_reference=self.uid, notes=self.summary,
            lines=[self._line(room)])
        reservation.save()
        self._price(reservation)
        Reservation.confirm([reservation])
        self.reservation = reservation
        self.state = 'confirmed'
        self.save()

    def _line(self, room):
        Line = Pool().get('hotel.reservation.line')
        return Line(room_type=room.type, room=room)

    def _price(self, reservation):
        from trytond.modules.hotel.exceptions import RateError
        Line = Pool().get('hotel.reservation.line')
        lines = []
        for line in reservation.lines:
            if self.channel.compute_prices:
                try:
                    line.compute(reservation.start, reservation.end)
                except RateError:
                    self._free_price(line, reservation)
            else:
                self._free_price(line, reservation)
            lines.append(line)
        Line.save(lines)

    @classmethod
    def _free_price(cls, line, reservation):
        from decimal import Decimal
        nights = max((reservation.end.date() - reservation.start.date()).days,
            1)
        line.billed_as = 'nightly'
        line.quantity = Decimal(nights)
        line.unit_price = Decimal(0)

    def release(self, save=True):
        "Free the room: cancel the reservation of the booking"
        Reservation = Pool().get('hotel.reservation')
        reservation = self.reservation
        if reservation and reservation.state in {'draft', 'confirmed'}:
            Reservation.cancel([reservation])
        self.reservation = None
        self.state = 'cancelled'
        if save:
            self.save()

    @classmethod
    @ModelView.button
    def retry(cls, bookings):
        for booking in bookings:
            booking.accept()


class Log(ModelSQL, ModelView):
    "Hotel Channel Log"
    __name__ = 'hotel.channel.log'
    _rec_name = 'name'

    name = fields.Function(
        fields.Char("Name"), 'get_name', searcher='search_name')
    channel = fields.Many2One(
        'hotel.channel', "Channel", required=True, ondelete='CASCADE',
        readonly=True)
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    mapping = fields.Many2One(
        'hotel.channel.mapping', "Room", ondelete='CASCADE', readonly=True)
    date = fields.DateTime("Date", required=True, readonly=True)
    direction = fields.Selection([
            ('import', "Import"),
            ('export', "Export"),
            ], "Direction", required=True, readonly=True)
    state = fields.Selection([
            ('success', "Success"),
            ('error', "Error"),
            ], "State", required=True, readonly=True)
    created = fields.Integer("Created", readonly=True)
    updated = fields.Integer("Updated", readonly=True)
    cancelled = fields.Integer("Cancelled", readonly=True)
    conflicts = fields.Integer("Conflicts", readonly=True)
    message = fields.Text("Message", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('date', 'DESC'), ('id', 'DESC')]

    def get_name(self, name):
        return "%s %s" % (
            self.channel.rec_name, self.date.strftime('%Y-%m-%d %H:%M'))

    @classmethod
    def search_name(cls, name, clause):
        return [('channel.name',) + tuple(clause[1:])]

    def get_company(self, name):
        return self.channel.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('channel.hotel.company',) + tuple(clause[1:])]


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    channel = fields.Many2One(
        'hotel.channel', "Channel", readonly=True, ondelete='SET NULL',
        help="The distribution channel the reservation comes from.")
    external_reference = fields.Char(
        "External Reference", readonly=True,
        help="The reference of the reservation on the channel.")

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('channel', None)
        default.setdefault('external_reference', None)
        return super().copy(reservations, default=default)


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('hotel.channel|synchronize_all',
                "Synchronize Hotel Channels"))


class Notification(metaclass=PoolMeta):
    __name__ = 'hotel.notification'

    @classmethod
    def _rules(cls):
        return super()._rules() + [cls._rule_channel_conflicts,
            cls._rule_channel_errors]

    @classmethod
    def _channel_text(cls, name, **kwargs):
        return gettext('hotel_channel_manager.' + name, **kwargs)

    @classmethod
    def _rule_channel_conflicts(cls, hotel, now):
        Booking = Pool().get('hotel.channel.booking')
        for booking in Booking.search([
                    ('channel.hotel', '=', hotel.id),
                    ('state', '=', 'conflict'),
                    ('end', '>=', now)]):
            room = booking.mapping.room
            yield cls._item(
                'alert', 'chconflict:%s' % booking.id,
                cls._channel_text(
                    'msg_ch_conflict_title', room=room.rec_name,
                    channel=booking.channel.rec_name),
                cls._channel_text(
                    'msg_ch_conflict', channel=booking.channel.rec_name,
                    summary=booking.summary or booking.uid,
                    start=booking.start.strftime('%Y-%m-%d'),
                    end=booking.end.strftime('%Y-%m-%d')),
                room, booking.start)

    @classmethod
    def _rule_channel_errors(cls, hotel, now):
        Log = Pool().get('hotel.channel.log')
        Mapping = Pool().get('hotel.channel.mapping')
        for mapping in Mapping.search([
                    ('channel.hotel', '=', hotel.id),
                    ('channel.active', '=', True),
                    ('import_url', '!=', None)]):
            logs = Log.search([('mapping', '=', mapping.id)], limit=1)
            if logs and logs[0].state == 'error':
                yield cls._item(
                    'warning', 'cherror:%s' % mapping.id,
                    cls._channel_text(
                        'msg_ch_error_title',
                        channel=mapping.channel.rec_name),
                    cls._channel_text(
                        'msg_ch_error', room=mapping.room.rec_name,
                        error=logs[0].message or ''),
                    mapping.room, logs[0].date)
