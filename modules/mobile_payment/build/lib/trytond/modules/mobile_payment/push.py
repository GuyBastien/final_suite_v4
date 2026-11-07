# This file is part of the Tryton mobile_payment module.
"""Push notification targets: managers receive the payment events on their
phone (ntfy) or in another system (JSON webhook), even when no Tryton client
is open."""
import datetime
import logging
import urllib.parse

from trytond.i18n import gettext
from trytond.model import DeactivableMixin, ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval, Id, If
from trytond.transaction import Transaction, without_check_access

from .exceptions import MobilePaymentError
from .tools import check_push_url, http_json

logger = logging.getLogger(__name__)

EVENTS = [
    ('succeeded', "Payment Received"),
    ('failed', "Payment Failed"),
    ('expired', "Payment Expired"),
    ('orphan', "Payment To Review"),
    ('manual', "Manual Confirmation"),
    ]
_ADMIN = Id('mobile_payment', 'group_mobile_payment_admin')


class Push(DeactivableMixin, ModelSQL, ModelView):
    "Mobile Payment Push Target"
    __name__ = 'mobile.payment.push'

    company = fields.Many2One(
        'company.company', "Company", required=True,
        help="Events of this company are sent to the target.")
    name = fields.Char(
        "Name", required=True,
        help="For example 'Manager phone' or 'Accounting webhook'.")
    kind = fields.Selection([
            ('ntfy', "Phone Push (ntfy)"),
            ('webhook', "Webhook (JSON)"),
            ], "Kind", required=True,
        help="ntfy: free push application for Android and iPhone "
        "(ntfy.sh or your own server). Webhook: the event is posted as JSON "
        "to any HTTPS address (chat bridge, accounting tool...).")
    url = fields.Char(
        "Address", required=True,
        help="HTTPS address. For ntfy, the topic address, for example "
        "https://ntfy.sh/my-private-topic: choose a long, hard to guess "
        "topic name. Local and private addresses are refused.")
    token = fields.Char(
        "Access Token",
        states={'invisible': ~Eval('context', {}).get(
                'groups', []).contains(_ADMIN)},
        help="Optional token sent as 'Authorization: Bearer'. Only the "
        "administrators of mobile payments can read it.")
    events = fields.MultiSelection(
        'get_events', "Events",
        help="Only these events are sent to the target.")
    last_sent = fields.DateTime("Last Sent", readonly=True)
    last_result = fields.Char(
        "Last Result", readonly=True,
        help="'OK' or the reason why the last delivery failed.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'send_test': {'icon': 'tryton-forward'},
                })

    @classmethod
    def get_events(cls):
        return EVENTS

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_kind(cls):
        return 'ntfy'

    @classmethod
    def default_events(cls):
        return [e for e, _ in EVENTS]

    @classmethod
    def validate(cls, targets):
        super().validate(targets)
        for target in targets:
            if not check_push_url(target.url):
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_push_url_invalid',
                        url=target.url))

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    (Eval('last_result', '') != '')
                    & (Eval('last_result', '') != 'OK'), 'danger', '')),
            ]

    # Delivery

    @classmethod
    def enqueue(cls, company, event, title, body, priority=3, data=None):
        """Queue the event for every matching target.

        The queue sends after the commit, so a slow or dead push server never
        delays the cashier and a rolled back payment sends nothing."""
        with without_check_access():
            targets = cls.search([('company', '=', company.id)])
            for target in targets:
                if event in (target.events or []):
                    cls.__queue__.send(
                        target, event, title, body, priority, data or {})

    def send(self, event, title, body, priority=3, data=None):
        "Deliver one event; the result is kept on the target, never raised."
        try:
            status = self._deliver(event, title, body, priority, data or {})
            ok = 200 <= status < 300
            result = 'OK' if ok else 'HTTP %s' % status
        except Exception as e:  # a target must never break the payment flow
            logger.warning("Push to %s failed", self.name, exc_info=True)
            result = str(e)[:200]
        with without_check_access():
            self.__class__.write([self], {
                    'last_sent': datetime.datetime.now(),
                    'last_result': result})
        return result

    def _deliver(self, event, title, body, priority, data):
        headers = {}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.kind == 'ntfy':
            parsed = urllib.parse.urlparse(self.url)
            topic = parsed.path.strip('/').split('/')[-1]
            root = '%s://%s/' % (parsed.scheme, parsed.netloc)
            payload = {
                'topic': topic, 'title': title, 'message': body,
                'priority': max(1, min(5, int(priority))),
                'tags': [event]}
            url = root
        else:
            payload = {
                'event': event, 'title': title, 'message': body,
                'priority': int(priority), 'data': data}
            url = self.url
        status, _ = http_json(url, data=payload, headers=headers, timeout=5)
        return status

    @classmethod
    @ModelView.button
    def send_test(cls, targets):
        for target in targets:
            result = target.send(
                'succeeded',
                gettext('mobile_payment.msg_push_test_title'),
                gettext('mobile_payment.msg_push_test_body',
                    name=target.name), 3)
            if result != 'OK':
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_push_test_failed',
                        name=target.name, error=result))

