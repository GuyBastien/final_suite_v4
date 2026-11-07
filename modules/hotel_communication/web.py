# This file is part of the Tryton hotel_communication module.
"Public pre-check-in page: the guest gives the estimated arrival time"
import datetime
from html import escape

from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.wsgi import app
from werkzeug.exceptions import NotFound
from werkzeug.wrappers import Response

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(hotel)s</title></head>
<body style="font-family:sans-serif;max-width:32em;margin:2em auto;padding:0 1em">
<h1>%(hotel)s</h1>
<p>%(guest)s - %(reservation)s<br>%(arrival)s &rarr; %(departure)s</p>
%(body)s
</body></html>"""

FORM = """<form method="post">
<label>Estimated arrival time<br>
<input type="time" name="eta" value="%(eta)s" required></label>
<p><button type="submit">Send</button></p>
</form>"""


def _page(reservation, body):
    return Response(PAGE % {
            'hotel': escape(reservation.hotel.name),
            'guest': escape(reservation.party.rec_name),
            'reservation': escape(reservation.number or ''),
            'arrival': escape(reservation.start.strftime('%Y-%m-%d %H:%M')),
            'departure': escape(reservation.end.strftime('%Y-%m-%d %H:%M')),
            'body': body,
            }, mimetype='text/html', headers={'Cache-Control': 'no-store'})


def _parse_time(value):
    try:
        return datetime.datetime.strptime(
            (value or '').strip()[:5], '%H:%M').time()
    except ValueError:
        return None


@app.route('/<string:database_name>/hotel/precheckin/<string:token>',
    methods=['GET', 'POST'])
def precheckin(request, database_name, token):
    "The token in the address is the only credential of the page"
    with Transaction().start(database_name, 0) as transaction:
        Reservation = Pool().get('hotel.reservation')
        reservation = Reservation.by_precheckin_token(token)
        if reservation is None:
            raise NotFound()
        if request.method == 'POST':
            eta = _parse_time(request.form.get('eta'))
            if eta is not None:
                Reservation.apply_precheckin(token, eta)
                transaction.commit()
                return _page(reservation, "<p>Thank you!</p>")
        current = reservation.precheckin_eta
        return _page(reservation, FORM % {
                'eta': current.strftime('%H:%M') if current else ''})
