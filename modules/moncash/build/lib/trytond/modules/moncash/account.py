# This file is part of the Tryton moncash module.
"""MonCash (Digicel Haiti) provider.

Uses the official MonCash REST API (the same one wrapped by the Python SDK
"moncashify"): OAuth2 client credentials, CreatePayment, then the customer
confirms on the MonCash page, then RetrieveOrderPayment.
"""
import base64
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval, Id

from trytond.modules.mobile_payment.exceptions import MobilePaymentError
from trytond.modules.mobile_payment.tools import http_json, number

REST = {
    True: 'https://sandbox.moncashbutton.digicelgroup.com/Api',
    False: 'https://moncashbutton.digicelgroup.com/Api'}
GATEWAY = {
    True: 'https://sandbox.moncashbutton.digicelgroup.com/Moncash-middleware',
    False: 'https://moncashbutton.digicelgroup.com/Moncash-middleware'}

_MONCASH = Eval('provider') == 'moncash'
_ADMIN = Id('mobile_payment', 'group_mobile_payment_admin')


class Account(metaclass=PoolMeta):
    __name__ = 'mobile.payment.account'

    moncash_client_id = fields.Char(
        "MonCash Client ID",
        states={
            'invisible': ~_MONCASH,
            'required': _MONCASH & Eval('active', True)},
        help="Client ID of the application created in the MonCash Business "
        "portal (sandbox or live, matching the Sandbox box).")
    moncash_client_secret = fields.Char(
        "MonCash Secret Key",
        states={
            'invisible': ~_MONCASH
            | ~Eval('context', {}).get('groups', []).contains(_ADMIN),
            'required': _MONCASH & Eval('active', True)},
        help="Secret key of the application. Only the administrators of "
        "mobile payments can read it.")

    @classmethod
    def get_providers(cls):
        return super().get_providers() + [('moncash', "MonCash (Digicel)")]

    # API helpers

    def _moncash_rest(self):
        return REST[bool(self.sandbox)]

    def _moncash_token(self):
        raw = '%s:%s' % (self.moncash_client_id, self.moncash_client_secret)
        status, data = http_json(
            self._moncash_rest() + '/oauth/token',
            data='scope=read,write&grant_type=client_credentials', form=True,
            headers={
                'Accept': 'application/json',
                'Authorization': 'Basic ' + base64.b64encode(
                    raw.encode()).decode()})
        if status != 200 or not isinstance(data, dict) or not data.get(
                'access_token'):
            raise MobilePaymentError(gettext(
                    'moncash.msg_auth_failed', status=status))
        return data['access_token']

    def _moncash_post(self, path, payload):
        token = self._moncash_token()
        return http_json(
            self._moncash_rest() + path, data=payload,
            headers={
                'Accept': 'application/json',
                'Authorization': 'Bearer ' + token})

    # Provider interface

    def _moncash_test(self):
        self._moncash_token()
        return gettext(
            'moncash.msg_test_ok',
            mode=gettext('moncash.msg_mode_sandbox' if self.sandbox
                else 'moncash.msg_mode_live'))

    def _moncash_create(self, tx):
        if tx.currency.code != 'HTG':
            raise MobilePaymentError(gettext(
                    'moncash.msg_currency', currency=tx.currency.code))
        status, data = self._moncash_post('/v1/CreatePayment', {
                'amount': number(tx.amount), 'orderId': tx.order_ref})
        token = (data or {}).get('payment_token', {}).get('token') if (
            isinstance(data, dict)) else None
        if status not in (200, 202) or not token:
            raise MobilePaymentError(gettext(
                    'moncash.msg_create_failed', status=status,
                    detail=str(data)[:200]))
        return {
            'checkout_url': '%s/Payment/Redirect?token=%s' % (
                GATEWAY[bool(self.sandbox)], token),
            'message': gettext('moncash.msg_waiting'),
            'expires_at': datetime.datetime.now() + datetime.timedelta(
                minutes=self.expiry_minutes),
            }

    def _moncash_check(self, tx):
        status, data = self._moncash_post(
            '/v1/RetrieveOrderPayment', {'orderId': tx.order_ref})
        return self._moncash_result(status, data)

    def _moncash_lookup(self, tx):
        """Ask MonCash about the receipt typed by the cashier.

        An answer is accepted only if MonCash says 'successful'; an unknown
        receipt gives 'unknown' (the caller may then fall back on the manual
        confirmation, when the account allows it)."""
        status, data = self._moncash_post(
            '/v1/RetrieveTransactionPayment', {'transactionId': tx.receipt})
        if status == 404 or (status == 200 and isinstance(data, dict)
                and not data.get('payment')):
            return {'status': 'unknown'}
        result = self._moncash_result(status, data)
        payment = (data or {}).get('payment') or {}
        if payment.get('reference'):
            result['reference'] = str(payment['reference'])
        result['provider_ref'] = tx.receipt
        return result

    @staticmethod
    def _moncash_result(status, data):
        "Map a Retrieve*Payment answer; never succeeds without 'successful'."
        if status == 404:
            return {'status': 'pending'}
        if status != 200 or not isinstance(data, dict):
            raise MobilePaymentError(gettext(
                    'moncash.msg_check_failed', status=status))
        payment = data.get('payment') or {}
        if not payment:
            return {'status': 'pending'}
        message = str(payment.get('message') or '')
        result = {
            'provider_ref': str(payment.get('transaction_id') or '') or None,
            'payer': str(payment.get('payer') or '') or None,
            'message': message,
            }
        if message.strip().lower() == 'successful':
            result['status'] = 'succeeded'
            if payment.get('cost') is not None:
                result['amount'] = Decimal(str(payment['cost']))
        else:
            result['status'] = 'pending'
        return result

    def _moncash_parse_return(self, params):
        "Map the return-URL parameters to our order, verified by MonCash."
        order = params.get('orderId') or params.get('order_id')
        if order:
            return order
        transaction_id = params.get('transactionId') or params.get(
            'transaction_id')
        if transaction_id:
            status, data = self._moncash_post(
                '/v1/RetrieveTransactionPayment',
                {'transactionId': transaction_id})
            if status == 200 and isinstance(data, dict):
                return (data.get('payment') or {}).get('reference')
        return None
