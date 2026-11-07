# This file is part of the Tryton mobile_payment module.
"""Helpers shared by the mobile payment providers."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from trytond.i18n import gettext

from .exceptions import MobilePaymentError

HAITI_PHONE = re.compile(r'^509\d{8}$')


def normalize_phone(value):
    """Return a Haitian MSISDN as 509XXXXXXXX or None.

    Accepts "+509 3712-3456", "00509 37123456" or the local 8 digits."""
    if not value:
        return None
    digits = re.sub(r'\D', '', value)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 8:
        digits = '509' + digits
    if not HAITI_PHONE.match(digits):
        raise MobilePaymentError(
            gettext('mobile_payment.msg_invalid_phone', phone=value))
    return digits


def number(amount):
    "JSON number for a Decimal: integer when the amount is whole."
    amount = Decimal(amount)
    return int(amount) if amount == amount.to_integral_value() else float(
        amount)


def http_json(url, *, data=None, headers=None, form=False, method=None,
        timeout=20):
    """Small JSON-over-HTTP helper (standard library only).

    Return (http_status, decoded_json_or_None). Network failures raise
    MobilePaymentError so that the cashier sees a readable message."""
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = data.encode()
            headers.setdefault(
                'Content-Type', 'application/x-www-form-urlencoded')
        else:
            body = json.dumps(data).encode()
            headers.setdefault('Content-Type', 'application/json')
    request = urllib.request.Request(
        url, data=body, headers=headers,
        method=method or ('POST' if body is not None else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MobilePaymentError(gettext(
                'mobile_payment.msg_network_error', error=str(e))) from e
    try:
        payload = json.loads(raw.decode() or 'null')
    except ValueError:
        payload = None
    return status, payload


def qr_png(text, scale=6):
    """PNG bytes of the QR code of `text`, or None when there is nothing to
    encode or the optional "segno" library is not installed."""
    if not text:
        return None
    try:
        import io

        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error='m').save(buf, kind='png', scale=scale, border=3)
    return buf.getvalue()


# USSD ---------------------------------------------------------------------

USSD_CODE = re.compile(r'^\*[0-9*]+#$')
USSD_TEMPLATE = re.compile(r'^\*[0-9*{}a-z_]+#$')
_PLACEHOLDERS = {'merchant', 'amount'}


def whole_amount(amount):
    """Amount as the integer string typed on a phone keypad.

    USSD menus take whole gourdes only: raise for centimes."""
    amount = Decimal(amount)
    if amount != amount.to_integral_value() or amount <= 0:
        raise MobilePaymentError(gettext(
                'mobile_payment.msg_ussd_amount', amount=amount))
    return str(int(amount))


def template_is_valid(template):
    "True for e.g. *202*{merchant}*{amount}# with only known placeholders."
    if not template or not USSD_TEMPLATE.match(template):
        return False
    found = set(re.findall(r'\{([a-z_]*)\}', template))
    stripped = re.sub(r'\{[a-z_]*\}', '', template)
    return found <= _PLACEHOLDERS and '{' not in stripped and (
        '}' not in stripped)


def tel_uri(code):
    "tel: link of a USSD code ('#' must be percent-encoded to be dialled)."
    return 'tel:' + urllib.parse.quote(code, safe='*+')


def whatsapp_url(phone, text):
    "Link that opens WhatsApp with the message ready for this number."
    if not phone:
        return None
    return 'https://wa.me/%s?text=%s' % (phone, urllib.parse.quote(text))


def sms_url(phone, text):
    "Link that opens the SMS application with the message ready."
    if not phone:
        return None
    return 'sms:+%s?body=%s' % (phone, urllib.parse.quote(text))


# Push ---------------------------------------------------------------------

def check_push_url(url):
    """Accept only public https addresses for push targets.

    The address is typed by an administrator, but a server must never be
    tricked into calling its own network: localhost, private addresses and
    names without a dot are refused."""
    import ipaddress
    parsed = urllib.parse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not host or '.' not in host or (
            host.endswith(('.local', '.internal', '.localhost'))):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global
