# This file is part of the Tryton hotel_communication module.
import datetime
import json
import re
import secrets
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import urlparse

from trytond.config import config
from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, Workflow, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.sendmail import sendmail
from trytond.transaction import Transaction

from .exceptions import CommunicationError

EVENTS = [
    ('confirmation', "Confirmation"),
    ('pre_arrival', "Pre-arrival"),
    ('thank_you', "Thank You"),
    ('cancellation', "Cancellation"),
    ('custom', "Custom"),
    ]
# Messages needed to run the stay: sent whatever the marketing consent.
# The others (thank you, custom) are sent only to guests who accepted
# marketing communications.
TRANSACTIONAL = {'confirmation', 'pre_arrival', 'cancellation'}
CHANNELS = [
    ('email', "E-mail"),
    ('sms', "SMS"),
    ]
PLACEHOLDERS = [
    'guest_name', 'reservation', 'hotel', 'arrival', 'departure', 'total',
    'currency', 'precheckin_url']
_PLACEHOLDER = re.compile(r'\{(\w+)\}')
SMS_LENGTH = 480
DATE_FORMAT = '%Y-%m-%d %H:%M'


def render_text(text, values):
    """Replace {name} by the values; unknown names are left untouched.

    Only plain names are substituted, so a template cannot reach any
    attribute of the objects of the system.
    """
    def replace(match):
        return values.get(match.group(1), match.group(0))
    return _PLACEHOLDER.sub(replace, text or '')


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    reminder_days = fields.Integer(
        "Reminder Days", required=True,
        domain=[('reminder_days', '>=', 0)],
        help="Number of days before the arrival when the pre-arrival "
        "message is sent. 0 disables it.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'create_default_templates': {},
                })

    @classmethod
    def default_reminder_days(cls):
        return 2

    @classmethod
    @ModelView.button
    def create_default_templates(cls, hotels):
        Template = Pool().get('hotel.message.template')
        for hotel in hotels:
            Template.create_defaults(hotel)


class Template(DeactivableMixin, ModelSQL, ModelView):
    "Hotel Message Template"
    __name__ = 'hotel.message.template'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    name = fields.Char("Name", required=True)
    event = fields.Selection(EVENTS, "Event", required=True)
    channel = fields.Selection(CHANNELS, "Channel", required=True)
    subject = fields.Char(
        "Subject", translate=True,
        states={'invisible': Eval('channel') != 'email'},
        help="Placeholders: " + ", ".join("{%s}" % p for p in PLACEHOLDERS))
    body = fields.Text(
        "Body", required=True, translate=True,
        help="Placeholders: " + ", ".join("{%s}" % p for p in PLACEHOLDERS)
        + ". The text is translated: the guest receives the language "
        "of the party.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_hotel_unique', Unique(t, t.hotel, t.name),
                'hotel_communication.msg_template_name_unique'),
            ]
        cls._order.insert(0, ('hotel', 'ASC'))
        cls._order.insert(1, ('event', 'ASC'))

    @classmethod
    def default_channel(cls):
        return 'email'

    @classmethod
    def default_event(cls):
        return 'custom'

    def get_company(self, name):
        return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]

    @classmethod
    def validate(cls, templates):
        super().validate(templates)
        for template in templates:
            if template.channel == 'email' and not template.subject:
                raise CommunicationError(gettext(
                        'hotel_communication.msg_template_no_subject',
                        template=template.rec_name))

    @classmethod
    def create_defaults(cls, hotel):
        "Create the e-mail templates missing for the hotel"
        existing = {
            (t.event, t.channel) for t in cls.search([
                    ('hotel', '=', hotel.id), ('active', 'in', [True, False]),
                    ])}
        to_create = []
        for event, _ in EVENTS:
            if event == 'custom' or (event, 'email') in existing:
                continue
            to_create.append({
                    'hotel': hotel.id,
                    'name': gettext(
                        'hotel_communication.msg_default_%s_name' % event),
                    'event': event,
                    'channel': 'email',
                    'subject': gettext(
                        'hotel_communication.msg_default_%s_subject' % event),
                    'body': gettext(
                        'hotel_communication.msg_default_%s_body' % event),
                    })
        return cls.create(to_create) if to_create else []

    def render(self, reservation):
        "Return the subject and body for the reservation, in the guest's language"
        lang = reservation.party.lang
        code = lang.code if lang else Transaction().language
        with Transaction().set_context(language=code):
            template = self.__class__(self.id)
            link = '{precheckin_url}' in (
                (template.subject or '') + (template.body or ''))
            values = reservation.communication_values(link=link)
            return (
                render_text(template.subject, values),
                render_text(template.body, values))


class Message(Workflow, ModelSQL, ModelView):
    "Hotel Message"
    __name__ = 'hotel.message'
    _rec_name = 'subject'

    company = fields.Many2One(
        'company.company', "Company", required=True, readonly=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True, readonly=True)
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", ondelete='SET NULL',
        readonly=True)
    party = fields.Many2One(
        'party.party', "Guest", readonly=True,
        context={'company': Eval('company', -1)}, depends=['company'])
    template = fields.Many2One(
        'hotel.message.template', "Template", ondelete='SET NULL',
        readonly=True)
    event = fields.Selection(EVENTS, "Event", required=True, readonly=True)
    channel = fields.Selection(CHANNELS, "Channel", required=True,
        readonly=True)
    recipient = fields.Char("Recipient", required=True, readonly=True)
    subject = fields.Char("Subject", readonly=True)
    body = fields.Text("Body", required=True, readonly=True)
    state = fields.Selection([
            ('queued', "Queued"),
            ('sent', "Sent"),
            ('failed', "Failed"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)
    sent_at = fields.DateTime("Sent At", readonly=True)
    attempts = fields.Integer("Attempts", readonly=True)
    error = fields.Text("Error", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('id', 'DESC')]
        cls._transitions |= {
            ('queued', 'sent'),
            ('queued', 'failed'),
            ('queued', 'cancelled'),
            ('failed', 'queued'),
            ('failed', 'cancelled'),
            }
        cls._buttons.update({
                'send': {
                    'invisible': Eval('state') != 'queued',
                    'depends': ['state'],
                    },
                'retry': {
                    'invisible': Eval('state') != 'failed',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': ~Eval('state').in_(['queued', 'failed']),
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'queued'

    @classmethod
    def default_attempts(cls):
        return 0

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree/field[@name="state"]', 'visual',
                If(Eval('state') == 'failed', 'danger',
                    If(Eval('state') == 'queued', 'warning',
                        If(Eval('state') == 'cancelled', 'muted', '')))),
            ]

    @classmethod
    def delete(cls, messages):
        for message in messages:
            if message.state == 'queued':
                raise CommunicationError(gettext(
                        'hotel_communication.msg_message_delete_queued',
                        message=message.rec_name))
        super().delete(messages)

    # Queueing

    @classmethod
    def _recipient(cls, party, channel):
        if channel == 'email':
            mechanism = party.contact_mechanism_get('email')
            return mechanism.email if mechanism else None
        mechanism = party.contact_mechanism_get(['mobile', 'phone'])
        return mechanism.value if mechanism else None

    @classmethod
    def queue(cls, reservation, event, channels=None, force=False):
        """Queue the messages of an event for a reservation.

        One message is queued per active template of the hotel for the
        event, when the guest has a contact for its channel. Non
        transactional events need the marketing consent of the guest.
        Return the created messages.
        """
        pool = Pool()
        Template = pool.get('hotel.message.template')
        party = reservation.party
        if getattr(party, 'anonymized', False):
            return []
        if (event not in TRANSACTIONAL and not force
                and not getattr(party, 'marketing_consent', False)):
            return []
        domain = [
            ('hotel', '=', reservation.hotel.id),
            ('event', '=', event),
            ]
        if channels:
            domain.append(('channel', 'in', list(channels)))
        values = []
        for template in Template.search(domain):
            recipient = cls._recipient(party, template.channel)
            if not recipient:
                continue
            subject, body = template.render(reservation)
            values.append({
                    'company': reservation.company.id,
                    'hotel': reservation.hotel.id,
                    'reservation': reservation.id,
                    'party': party.id,
                    'template': template.id,
                    'event': event,
                    'channel': template.channel,
                    'recipient': recipient,
                    'subject': subject if template.channel == 'email'
                    else None,
                    'body': body,
                    })
        return cls.create(values) if values else []

    # Sending

    @classmethod
    def _smtp_from(cls, message):
        from_ = config.get('email', 'from')
        if not from_:
            mechanism = (
                message.hotel.party.contact_mechanism_get('email')
                if message.hotel.party else None)
            from_ = mechanism.email if mechanism else None
        if not from_:
            raise CommunicationError(gettext(
                    'hotel_communication.msg_no_sender',
                    hotel=message.hotel.rec_name))
        return from_

    def _send_email(self):
        from_ = self._smtp_from(self)
        msg = MIMEText(self.body, _charset='utf-8')
        msg['Subject'] = Header(self.subject or '', 'utf-8')
        msg['From'] = formataddr((self.hotel.name, from_))
        msg['To'] = self.recipient
        sendmail(from_, [self.recipient], msg, strict=True)

    def _send_sms(self):
        url = config.get('hotel_communication', 'sms_url')
        token = config.get('hotel_communication', 'sms_token')
        if not url:
            raise CommunicationError(gettext(
                    'hotel_communication.msg_no_sms_url'))
        if urlparse(url).scheme not in {'http', 'https'}:
            raise CommunicationError(gettext(
                    'hotel_communication.msg_sms_url_scheme', url=url))
        payload = json.dumps({
                'to': self.recipient,
                'message': self.body[:SMS_LENGTH],
                'sender': self.hotel.name,
                }).encode('utf-8')
        request = urllib.request.Request(
            url, data=payload, method='POST',
            headers={'Content-Type': 'application/json'})
        if token:
            request.add_header('Authorization', 'Bearer %s' % token)
        with urllib.request.urlopen(request, timeout=15) as response:
            status = getattr(response, 'status', 200)
            if not 200 <= status < 300:
                raise CommunicationError(gettext(
                        'hotel_communication.msg_sms_status', status=status))

    def _deliver(self):
        "Send the message; return the error text or None"
        try:
            if self.channel == 'email':
                self._send_email()
            else:
                self._send_sms()
        except Exception as exception:  # the failure is recorded
            return (str(exception) or exception.__class__.__name__)[:1000]

    @classmethod
    @ModelView.button
    def send(cls, messages):
        sent, failed = [], []
        for message in messages:
            if message.state != 'queued':
                continue
            error = message._deliver()
            (failed if error else sent).append((message, error))
        now = datetime.datetime.now()
        for message, _ in sent:
            cls.write([message], {
                    'state': 'sent', 'sent_at': now, 'error': None,
                    'attempts': (message.attempts or 0) + 1})
        for message, error in failed:
            cls.write([message], {
                    'state': 'failed', 'error': error,
                    'attempts': (message.attempts or 0) + 1})

    @classmethod
    @ModelView.button
    @Workflow.transition('queued')
    def retry(cls, messages):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, messages):
        pass

    @classmethod
    def send_queued(cls, limit=200):
        "Cron: send the queued messages"
        cls.send(cls.search([('state', '=', 'queued')], order=[('id', 'ASC')],
                limit=limit))

    @classmethod
    def generate_reminders(cls):
        "Cron: queue the pre-arrival messages"
        pool = Pool()
        Hotel = pool.get('hotel.hotel')
        Reservation = pool.get('hotel.reservation')
        today = datetime.date.today()
        for hotel in Hotel.search([('reminder_days', '>', 0)]):
            first = datetime.datetime.combine(
                today + datetime.timedelta(days=hotel.reminder_days),
                datetime.time.min)
            reservations = Reservation.search([
                    ('hotel', '=', hotel.id),
                    ('state', '=', 'confirmed'),
                    ('start', '>=', first),
                    ('start', '<', first + datetime.timedelta(days=1)),
                    ])
            for reservation in reservations:
                if cls.search_count([
                            ('reservation', '=', reservation.id),
                            ('event', '=', 'pre_arrival'),
                            ]):
                    continue
                cls.queue(reservation, 'pre_arrival')


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.extend([
                ('hotel.message|send_queued', "Send Hotel Messages"),
                ('hotel.message|generate_reminders',
                    "Queue Pre-arrival Messages"),
                ])


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    messages = fields.One2Many(
        'hotel.message', 'reservation', "Messages", readonly=True)
    precheckin_token = fields.Char(
        "Pre-check-in Token", readonly=True,
        help="Secret of the pre-check-in address given to the guest.")
    precheckin_at = fields.DateTime("Pre-checked-in At", readonly=True)
    precheckin_eta = fields.Time("Estimated Arrival Time", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('precheckin_token_unique', Unique(t, t.precheckin_token),
                'hotel_communication.msg_precheckin_token_unique'),
            ]
        cls._buttons.update({
                'send_precheckin': {
                    'invisible': ~Eval('state').in_(['draft', 'confirmed']),
                    'depends': ['state'],
                    },
                })

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        for name in ['messages', 'precheckin_token', 'precheckin_at',
                'precheckin_eta']:
            default.setdefault(name, None)
        return super().copy(reservations, default=default)

    def ensure_precheckin_token(self):
        if not self.precheckin_token:
            self.__class__.write(
                [self], {'precheckin_token': secrets.token_urlsafe(24)})
        return self.precheckin_token

    def get_precheckin_url(self):
        base = (config.get('hotel_communication', 'base_url') or '').rstrip('/')
        token = self.ensure_precheckin_token()
        return '%s/%s/hotel/precheckin/%s' % (
            base, Transaction().database.name, token)

    def communication_values(self, link=False):
        values = {
            'guest_name': self.party.rec_name,
            'reservation': self.number or '',
            'hotel': self.hotel.name,
            'arrival': self.start.strftime(DATE_FORMAT),
            'departure': self.end.strftime(DATE_FORMAT),
            'total': str(self.total_amount),
            'currency': self.company.currency.code or '',
            }
        # The link is created only when a template uses it
        values['precheckin_url'] = self.get_precheckin_url() if link else ''
        return values

    @classmethod
    def _queue(cls, reservations, event):
        Message = Pool().get('hotel.message')
        for reservation in reservations:
            Message.queue(reservation, event)

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, reservations):
        super().confirm(reservations)
        cls._queue(reservations, 'confirmation')

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, reservations):
        super().cancel(reservations)
        cls._queue(reservations, 'cancellation')

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_out')
    def check_out(cls, reservations):
        super().check_out(reservations)
        cls._queue(reservations, 'thank_you')

    @classmethod
    @ModelView.button
    def send_precheckin(cls, reservations):
        "Send the pre-arrival message with the pre-check-in link"
        Message = Pool().get('hotel.message')
        for reservation in reservations:
            messages = Message.queue(reservation, 'pre_arrival')
            if not messages:
                raise CommunicationError(gettext(
                        'hotel_communication.msg_precheckin_no_message',
                        reservation=reservation.rec_name))

    @classmethod
    def by_precheckin_token(cls, token):
        "Return the open reservation of the pre-check-in token, if any"
        if not token or len(token) > 100:
            return None
        reservations = cls.search([
                ('precheckin_token', '=', token),
                ('state', 'in', ['draft', 'confirmed']),
                ], limit=1)
        return reservations[0] if reservations else None

    @classmethod
    def apply_precheckin(cls, token, eta):
        """Record the estimated arrival time (a datetime.time) given by the
        guest. Return the reservation or None for an unknown token."""
        reservation = cls.by_precheckin_token(token)
        if reservation is None:
            return None
        cls.write([reservation], {
                'precheckin_eta': eta,
                'precheckin_at': datetime.datetime.now(),
                })
        return reservation
