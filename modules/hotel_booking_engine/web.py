# This file is part of the Tryton hotel_booking_engine module.
"""Public booking pages of a hotel.

The secret token of the hotel is the only credential. Every input is
validated by the model; every output is HTML-escaped.

  GET  /<db>/hotel/booking/<token>/                 search page
  GET  /<db>/hotel/booking/<token>/availability.json  JSON search
  POST /<db>/hotel/booking/<token>/reserve          reservation
       (Accept: application/json returns JSON instead of HTML)
"""
import datetime
import json
from decimal import Decimal
from html import escape

from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.wsgi import app
from werkzeug.exceptions import BadRequest, NotFound
from werkzeug.wrappers import Response

from .exceptions import BookingError

BASE = '/<string:database_name>/hotel/booking/<string:token>'

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>body{font-family:sans-serif;max-width:40em;margin:1.5em auto;padding:0 1em}
fieldset{margin:1em 0}label{display:block;margin:.4em 0}
.error{color:#a00}.card{border:1px solid #ccc;border-radius:6px;padding:.6em;margin:.6em 0}</style>
</head><body><h1>%(title)s</h1>%(body)s</body></html>"""


def _date(value):
    try:
        return datetime.datetime.strptime(
            (value or '').strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _int(value, default):
    try:
        return int((value or '').strip())
    except (ValueError, AttributeError):
        return default


def _hotel(token):
    Hotel = Pool().get('hotel.hotel')
    hotel = Hotel.booking_by_token(token)
    if hotel is None:
        raise NotFound()
    return hotel


def _params(args):
    return {
        'arrival': _date(args.get('arrival')),
        'departure': _date(args.get('departure')),
        'adults': _int(args.get('adults'), 1),
        'children': _int(args.get('children'), 0),
        'rooms': _int(args.get('rooms'), 1),
        }


def _html(hotel, body, status=200):
    return Response(
        PAGE % {'title': escape(hotel.name), 'body': body},
        status=status, mimetype='text/html',
        headers={'Cache-Control': 'no-store'})


def _json(data, status=200):
    def default(value):
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError(value)
    return Response(
        json.dumps(data, default=default), status=status,
        mimetype='application/json', headers={'Cache-Control': 'no-store'})


def _wants_json(request):
    "True when the client (the mobile application) asks for JSON"
    return request.accept_mimetypes.best == 'application/json'


def _search_form(values):
    def val(name):
        value = values.get(name)
        return escape(str(value)) if value is not None else ''
    return """<form method="get"><fieldset><legend>Search</legend>
<label>Arrival <input type="date" name="arrival" value="%s" required></label>
<label>Departure <input type="date" name="departure" value="%s" required></label>
<label>Adults <input type="number" name="adults" min="1" max="50" value="%s"></label>
<label>Children <input type="number" name="children" min="0" max="50" value="%s"></label>
<label>Rooms <input type="number" name="rooms" min="1" max="20" value="%s"></label>
<button type="submit">Search</button></fieldset></form>""" % (
        val('arrival'), val('departure'), val('adults') or 1,
        val('children') or 0, val('rooms') or 1)


@app.route(BASE + '/', methods=['GET'])
def booking_page(request, database_name, token):
    with Transaction().start(database_name, 0, readonly=True):
        hotel = _hotel(token)
        params = _params(request.args)
        body = _search_form(params)
        if params['arrival'] and params['departure']:
            try:
                offers = hotel.booking_search(**params)
            except BookingError as error:
                body += '<p class="error">%s</p>' % escape(str(error))
            else:
                if not offers:
                    body += '<p>No room available for these dates.</p>'
                for offer in offers:
                    body += _offer(offer, params)
        return _html(hotel, body)


def _offer(offer, params):
    hidden = ''.join(
        '<input type="hidden" name="%s" value="%s">' % (
            name, escape(str(params[name]))) for name in params)
    return """<div class="card"><strong>%(name)s</strong>
<p>%(description)s</p>
<p>%(nights)s night(s) - %(night_price)s per night - total
<strong>%(total)s %(currency)s</strong> (%(available)s available)</p>
<form method="post" action="reserve">%(hidden)s
<input type="hidden" name="room_type" value="%(id)s">
<label>First name <input name="first_name" maxlength="60"></label>
<label>Last name <input name="last_name" maxlength="60" required></label>
<label>E-mail <input type="email" name="email" maxlength="120" required></label>
<label>Phone <input name="phone" maxlength="40"></label>
<label>Notes <textarea name="notes" maxlength="500"></textarea></label>
<button type="submit">Book</button></form></div>""" % {
        'name': escape(offer['name']),
        'description': escape(offer['description']),
        'nights': offer['nights'],
        'night_price': escape(str(offer['night_price'])),
        'total': escape(str(offer['total'])),
        'currency': escape(offer['currency'] or ''),
        'available': offer['available'],
        'id': offer['id'],
        'hidden': hidden}


@app.route(BASE + '/availability.json', methods=['GET'])
def booking_availability(request, database_name, token):
    with Transaction().start(database_name, 0, readonly=True):
        hotel = _hotel(token)
        params = _params(request.args)
        try:
            offers = hotel.booking_search(**params)
        except BookingError as error:
            return _json({'error': str(error)}, 400)
        return _json({
                'hotel': hotel.name,
                'arrival': params['arrival'].isoformat(),
                'departure': params['departure'].isoformat(),
                'offers': offers})


@app.route(BASE + '/reserve', methods=['POST'])
def booking_reserve(request, database_name, token):
    with Transaction().start(database_name, 0) as transaction:
        hotel = _hotel(token)
        form = request.form
        params = _params(form)
        if not params['arrival'] or not params['departure']:
            raise BadRequest()
        try:
            room_type = int(form.get('room_type', ''))
        except ValueError:
            raise BadRequest()
        guest = {name: form.get(name, '') for name in (
                'first_name', 'last_name', 'email', 'phone')}
        try:
            reservation = hotel.booking_reserve(
                params['arrival'], params['departure'], params['adults'],
                params['children'], {room_type: params['rooms']}, guest,
                notes=form.get('notes', ''))
        except BookingError as error:
            transaction.rollback()
            if _wants_json(request):
                return _json({
                        'error': str(error),
                        'code': type(error).__name__}, 409)
            return _html(
                hotel, '<p class="error">%s</p>' % escape(str(error)), 409)
        transaction.commit()
        if _wants_json(request):
            return _json({
                    'number': reservation.number or '',
                    'state': reservation.state,
                    'deposit_requested': reservation.deposit_requested,
                    'currency': hotel.company.currency.code or ''})
        body = '<p>Thank you! Your reservation <strong>%s</strong> is %s.</p>' % (
            escape(reservation.number or ''),
            'confirmed' if reservation.state == 'confirmed'
            else 'received and will be confirmed shortly')
        if reservation.deposit_requested:
            body += '<p>Deposit requested: %s %s</p>' % (
                escape(str(reservation.deposit_requested)),
                escape(hotel.company.currency.code or ''))
        return _html(hotel, body)
