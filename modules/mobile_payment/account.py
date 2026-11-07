# This file is part of the Tryton mobile_payment module.
import re

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, Id, If
from trytond.transaction import Transaction, without_check_access

from .exceptions import MobilePaymentError
from .tools import USSD_CODE, template_is_valid, whole_amount

_ADMIN = Id('mobile_payment', 'group_mobile_payment_admin')
ADMIN_ONLY = {
    'invisible': ~Eval('context', {}).get('groups', []).contains(_ADMIN)}


class PaymentMethod(metaclass=PoolMeta):
    __name__ = 'sale.point.payment.method'

    mobile = fields.Boolean(
        "Mobile Payment", readonly=True,
        help="This method is used by a mobile payment account. Payments on "
        "it are created by the confirmation of the provider, not typed by "
        "the cashier.")


class Account(DeactivableMixin, ModelSQL, ModelView):
    "Mobile Payment Account"
    __name__ = 'mobile.payment.account'

    company = fields.Many2One(
        'company.company', "Company", required=True,
        help="The company that receives the money.")
    name = fields.Char(
        "Name", required=True,
        help="Name shown to the cashier, for example 'MonCash Boutique'.")
    provider = fields.Selection(
        'get_providers', "Provider", required=True,
        help="Mobile money operator. The list is extended by the provider "
        "modules (moncash, natcash).")
    sandbox = fields.Boolean(
        "Sandbox",
        help="Use the test platform of the operator. No real money moves. "
        "Untick only after the operator activated the live credentials.")
    method = fields.Many2One(
        'sale.point.payment.method', "Payment Method", required=True,
        domain=[
            ('company', '=', Eval('company', -1)),
            ('cash', '=', False)],
        help="Payment method on which confirmed payments are booked. Its "
        "account is the clearing account of the operator.")
    expiry_minutes = fields.Integer(
        "Expiry (min)", required=True,
        domain=[('expiry_minutes', '>=', 1), ('expiry_minutes', '<=', 1440)],
        help="A request still unpaid after this delay is expired by the "
        "scheduler (after one last check with the operator).")
    ussd_enabled = fields.Boolean(
        "USSD Payment",
        help="Let the cashier offer a payment by USSD: the customer dials a "
        "code on the phone instead of scanning the QR code. Works without "
        "data connection on the customer side.")
    ussd_menu = fields.Char(
        "USSD Menu Code",
        states={
            'invisible': ~Eval('ussd_enabled', False),
            'required': Eval('ussd_enabled', False)},
        help="Code that opens the mobile money menu of the operator, "
        "for example *202#.")
    merchant_code = fields.Char(
        "Merchant Code",
        states={
            'invisible': ~Eval('ussd_enabled', False),
            'required': Eval('ussd_enabled', False)},
        help="Merchant number or code given by the operator when the "
        "merchant account was opened. The customer types it to pay you.")
    ussd_template = fields.Char(
        "USSD Shortcut",
        states={'invisible': ~Eval('ussd_enabled', False)},
        help="Optional direct code given by the operator, for example "
        "*202*{merchant}*{amount}#. {merchant} is replaced by the merchant "
        "code and {amount} by the amount in whole gourdes. Leave empty if "
        "the customer must follow the menu: the instructions then show the "
        "menu code, the merchant code and the amount.")
    allow_manual = fields.Boolean(
        "Allow Manual Confirmation",
        states={'invisible': ~Eval('ussd_enabled', False)},
        help="When the operator cannot confirm a receipt number by itself, "
        "let a mobile payment administrator confirm it by hand. The payment "
        "is then booked and flagged \"To Review\" until someone has compared "
        "it with the operator statement.")
    pending_count = fields.Function(
        fields.Integer("Pending Payments"), 'get_pending_count')
    last_test = fields.Text(
        "Last Connection Test", readonly=True,
        help="Result of the last test of the credentials.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_company_unique', Unique(t, t.company, t.name),
                'mobile_payment.msg_account_name_unique'),
            ]
        cls._buttons.update({
                'test_connection': {
                    'icon': 'tryton-refresh',
                    'depends': ['provider']},
                })

    @classmethod
    def get_providers(cls):
        return [(None, "")]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def _flag_methods(cls, accounts):
        Method = Pool().get('sale.point.payment.method')
        methods = [a.method for a in accounts if not a.method.mobile]
        if methods:
            Method.write(methods, {'mobile': True})

    @classmethod
    def create(cls, vlist):
        accounts = super().create(vlist)
        cls._flag_methods(accounts)
        return accounts

    @classmethod
    def write(cls, *args):
        super().write(*args)
        actions = iter(args)
        for accounts, values in zip(actions, actions):
            if 'method' in values:
                cls._flag_methods(accounts)

    @classmethod
    def default_sandbox(cls):
        return True

    @classmethod
    def default_expiry_minutes(cls):
        return 15

    @classmethod
    def default_ussd_menu(cls):
        return '*202#'

    @classmethod
    def validate(cls, accounts):
        super().validate(accounts)
        for account in accounts:
            if not account.ussd_enabled:
                continue
            if not USSD_CODE.match(account.ussd_menu or ''):
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_ussd_menu_invalid',
                        code=account.ussd_menu or ''))
            if not re.match(r'^\w{2,20}$', account.merchant_code or ''):
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_merchant_invalid',
                        code=account.merchant_code or ''))
            if account.ussd_template and not template_is_valid(
                    account.ussd_template):
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_ussd_template_invalid',
                        template=account.ussd_template))

    # USSD

    def ussd_dial(self, amount):
        "Code the customer dials: the shortcut if any, else the menu code."
        if not self.ussd_template:
            return self.ussd_menu
        code = self.ussd_template.format(
            merchant=self.merchant_code, amount=whole_amount(amount))
        if not USSD_CODE.match(code):
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_ussd_bad_dial', code=code))
        return code

    def ussd_steps(self, amount, currency):
        "Instructions to read to the customer."
        code = self.ussd_dial(amount)
        if self.ussd_template:
            return gettext(
                'mobile_payment.msg_ussd_steps_shortcut', code=code,
                amount=amount, currency=currency, provider=self.name)
        return gettext(
            'mobile_payment.msg_ussd_steps', code=code,
            merchant=self.merchant_code, amount=amount, currency=currency,
            provider=self.name)

    def provider_supports(self, action):
        "True when the provider module implements this action."
        return hasattr(self, '_%s_%s' % (self.provider, action))

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(Eval('sandbox', True), 'warning', '')),
            ]

    @classmethod
    def get_pending_count(cls, accounts, name):
        Tx = Pool().get('mobile.payment.transaction')
        counts = {a.id: 0 for a in accounts}
        for tx in Tx.search([
                    ('account', 'in', [a.id for a in accounts]),
                    ('state', '=', 'pending')]):
            counts[tx.account.id] += 1
        return counts

    def provider_call(self, action, transaction=None):
        """Dispatch to the provider implementation `_<provider>_<action>`."""
        method = getattr(self, '_%s_%s' % (self.provider, action), None)
        if method is None:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_no_provider', provider=self.provider,
                    action=action))
        # the cashier must not read the credentials but must be able to use them
        with without_check_access():
            return (method(transaction) if transaction is not None
                else method())

    def provider_call_params(self, params):
        """Order id found in the return-URL parameters, or None.

        The provider may verify a signature here and raise
        MobilePaymentError for a forged call."""
        method = getattr(self, '_%s_parse_return' % self.provider, None)
        with without_check_access():
            return method(params) if method else None

    @classmethod
    @ModelView.button
    def test_connection(cls, accounts):
        for account in accounts:
            try:
                result = account.provider_call('test')
            except MobilePaymentError as e:
                result = gettext(
                    'mobile_payment.msg_test_failed', error=str(e))
            cls.write([account], {'last_test': result})

