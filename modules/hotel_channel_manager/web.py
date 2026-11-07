# This file is part of the Tryton hotel_channel_manager module.
"Public iCal address of the rooms occupancy"
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.wsgi import app
from werkzeug.exceptions import NotFound
from werkzeug.wrappers import Response


@app.route('/<string:database_name>/hotel/ical/<string:token>.ics',
    methods=['GET'])
def ical_export(request, database_name, token):
    "The token in the address is the only credential of the calendar"
    with Transaction().start(database_name, 0, readonly=True):
        Mapping = Pool().get('hotel.channel.mapping')
        text = Mapping.ical_by_token(token)
    if text is None:
        raise NotFound()
    return Response(
        text, mimetype='text/calendar', headers={
            'Cache-Control': 'no-cache'})
