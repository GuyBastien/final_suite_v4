# This file is part of the Tryton mobile_payment module.
"""Return URL of the operators.

The customer (or the operator) is sent back to
https://<server>/<database>/mobile_payment/<provider>/return after paying.
Nothing in the request is trusted: it only triggers a server-side
verification with the operator, so a forged call cannot create a payment.
"""
import logging

from werkzeug.wrappers import Response

from trytond.pool import Pool
from trytond.protocols.wrappers import with_pool, with_transaction
from trytond.wsgi import app

logger = logging.getLogger(__name__)

PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<body style="font-family:sans-serif;text-align:center;margin-top:4rem">
<h2>%(title)s</h2><p>%(text)s</p></body>"""


@app.route(
    '/<string:database_name>/mobile_payment/<string:provider>/return',
    methods={'GET', 'POST'})
@with_pool
@with_transaction(readonly=False)
def mobile_payment_return(request, pool, provider):
    Transaction = pool.get('mobile.payment.transaction')
    params = {**request.args.to_dict(), **request.form.to_dict()}
    count = Transaction.handle_return(provider, params)
    text = ("Payment received. You can close this page."
        if count else "Thank you. The payment is being verified.")
    return Response(
        PAGE % {'title': "MonCash / NatCash", 'text': text},
        mimetype='text/html')
