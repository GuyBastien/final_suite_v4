# This file is part of the Tryton natcash module.
"""NatCash (Natcom Haiti) provider.

Natcom does not publish its merchant API openly. The wire protocol (request
signing, checkout page, signed browser callback, transaction check) is
delegated to the open-source Python SDK ``natcashify`` (MIT), which must be
installed (``pip install natcash-tryton[sdk]``). Validate your merchant
credentials with the Natcom business team before going live.
"""
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval, Id

from trytond.modules.mobile_payment.exceptions import MobilePaymentError

_NATCASH = Eval('provider') == 'natcash'
_ADMIN = Id('mobile_payment', 'group_mobile_payment_admin')
_SECRET = {
    'invisible': ~_NATCASH
    | ~Eval('context', {}).get('groups', []).contains(_ADMIN),
    'required': _NATCASH & Eval('active', True)}
_PLAIN = {
    'invisible': ~_NATCASH,
    'required': _NATCASH & Eval('active', True)}


def _sdk():
    try:
        import natcashify
    except ImportError as e:
        raise MobilePaymentError(gettext('natcash.msg_sdk_missing')) from e
    return natcashify


class Account(metaclass=PoolMeta):
    __name__ = 'mobile.payment.account'

    natcash_partner_code = fields.Char(
        "NatCash Partner Code", states=_PLAIN,
        help="Partner code given by Natcom for your merchant account.")
    natcash_function_code = fields.Char(
        "NatCash Function Code", states=_SECRET,
        help="Function code given by Natcom. Only the administrators of "
        "mobile payments can read it.")
    natcash_username = fields.Char(
        "NatCash Username", states=_PLAIN,
        help="API username of the merchant account.")
    natcash_password = fields.Char(
        "NatCash Password", states=_SECRET,
        help="API password of the merchant account.")
    natcash_private_key = fields.Char(
        "NatCash Private Key", states=_SECRET,
        help="Private key used to sign the requests and to verify the "
        "callback of NatCash.")
    natcash_callback_url = fields.Char(
        "NatCash Callback URL", states=_PLAIN,
        help="Where NatCash sends the customer back after paying: "
        "https://<server>/<database>/mobile_payment/natcash/return")
    natcash_payer_pays_fee = fields.Boolean(
        "Customer Pays the Fee", states={'invisible': ~_NATCASH},
        help="The NatCash fee is added to the amount paid by the customer.")
    natcash_language = fields.Selection([
            ('ht', "Haitian Creole"),
            ('fr', "French"),
            ('en', "English"),
            ], "NatCash Page Language",
        states={'invisible': ~_NATCASH, 'required': _NATCASH},
        help="Language of the NatCash payment page.")

    @classmethod
    def default_natcash_payer_pays_fee(cls):
        return True

    @classmethod
    def default_natcash_language(cls):
        return 'ht'

    @classmethod
    def get_providers(cls):
        return super().get_providers() + [('natcash', "NatCash (Natcom)")]

    # SDK helpers

    def _natcash_transport(self):
        "Hook: None uses the SDK default transport (tests inject a fake)."
        return None

    def _natcash_client(self):
        sdk = _sdk()
        try:
            config = sdk.MerchantConfig(
                private_key=self.natcash_private_key or '',
                partner_code=self.natcash_partner_code or '',
                function_code=self.natcash_function_code or '',
                username=self.natcash_username or '',
                password=self.natcash_password or '',
                callback_url=self.natcash_callback_url or '',
                payer_pays_fee=bool(self.natcash_payer_pays_fee),
                language=sdk.Language(self.natcash_language or 'ht'))
        except sdk.ConfigurationError as e:
            raise MobilePaymentError(gettext(
                    'natcash.msg_config', error=str(e))) from e
        return sdk.NatcashClient(
            config, sandbox=bool(self.sandbox),
            transport=self._natcash_transport())

    @staticmethod
    def _natcash_error(e):
        return MobilePaymentError(gettext(
                'natcash.msg_api_error', error=str(e)))

    # Provider interface

    def _natcash_test(self):
        client = self._natcash_client()
        return gettext(
            'natcash.msg_test_ok',
            mode=gettext('natcash.msg_mode_sandbox' if client.sandbox
                else 'natcash.msg_mode_live'))

    def _natcash_create(self, tx):
        sdk = _sdk()
        if tx.currency.code != 'HTG':
            raise MobilePaymentError(gettext(
                    'natcash.msg_currency', currency=tx.currency.code))
        client = self._natcash_client()
        try:
            payment = client.create_payment(
                tx.order_ref, Decimal(tx.amount),
                phone_number=tx.phone or None,
                skip_phone_input=bool(tx.phone))
        except sdk.NatcashError as e:
            raise self._natcash_error(e) from e
        return {
            'checkout_url': payment.checkout_url,
            'message': gettext('natcash.msg_waiting'),
            'expires_at': datetime.datetime.now() + min(
                payment.expires_in,
                datetime.timedelta(minutes=self.expiry_minutes)),
            }

    def _natcash_check(self, tx):
        sdk = _sdk()
        client = self._natcash_client()
        try:
            transaction = client.get_transaction(tx.order_ref)
        except sdk.TransactionNotFoundError:
            return {'status': 'pending'}
        except sdk.NatcashError as e:
            raise self._natcash_error(e) from e
        result = {
            'provider_ref': transaction.transaction_id,
            'payer': transaction.payer or None,
            'amount': transaction.amount,
            }
        if transaction.status is sdk.TransactionStatus.SUCCESS:
            result['status'] = 'succeeded'
        elif transaction.status is sdk.TransactionStatus.FAILED:
            result['status'] = 'failed'
            result['message'] = gettext(
                'natcash.msg_refused', code=transaction.response_code)
        else:
            result['status'] = 'pending'
        return result

    def _natcash_parse_return(self, params):
        "Verify the signature of the browser callback; return the order."
        sdk = _sdk()
        client = self._natcash_client()
        try:
            callback = client.parse_callback(params)
        except sdk.InvalidSignatureError as e:
            raise MobilePaymentError(gettext('natcash.msg_forged')) from e
        return callback.order_number
