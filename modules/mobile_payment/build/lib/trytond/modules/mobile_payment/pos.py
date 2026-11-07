# This file is part of the Tryton mobile_payment module.
from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.modules.currency.fields import Monetary
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction
from trytond.wizard import Button, StateTransition, StateView, Wizard

from .exceptions import MobilePaymentError


class Payment(metaclass=PoolMeta):
    __name__ = 'sale.point.payment'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        # A mobile method can only be booked by the confirmation of the
        # operator, never typed by the cashier.
        cls.method.domain = list(cls.method.domain) + [
            If(Eval('context', {}).get('mobile_confirm', False),
                [], [('mobile', '=', False)])]


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.point.sale'

    mobile_transactions = fields.One2Many(
        'mobile.payment.transaction', 'sale', "Mobile Payments",
        readonly=True,
        help="MonCash, NatCash and other mobile payments of this ticket.")
    mobile_pending = fields.Function(
        fields.Boolean("Mobile Payment Pending"), 'get_mobile_pending')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_mobile': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                })

    @classmethod
    def get_mobile_pending(cls, sales, name):
        return {s.id: any(t.state == 'pending' for t in s.mobile_transactions)
            for s in sales}

    @classmethod
    @ModelView.button_action('mobile_payment.wizard_pay')
    def pay_mobile(cls, sales):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, sales):
        for sale in sales:
            if any(t.state == 'pending' for t in sale.mobile_transactions):
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_cancel_pending',
                        sale=sale.rec_name))
        super().cancel(sales)


class PayStart(ModelView):
    "Mobile Payment"
    __name__ = 'mobile.payment.pay.start'

    document = fields.Char("Document", readonly=True)
    target_model = fields.Char("Model", readonly=True)
    target_id = fields.Integer("Record", readonly=True)
    company = fields.Many2One('company.company', "Company", readonly=True)
    currency = fields.Many2One('currency.currency', "Currency", readonly=True)
    account = fields.Many2One(
        'mobile.payment.account', "Operator Account", required=True,
        domain=[('company', '=', Eval('company', -1))],
        help="MonCash, NatCash... account that receives the money.")
    amount = Monetary(
        "Amount", currency='currency', digits='currency', required=True,
        domain=[('amount', '>', 0)],
        help="Amount asked to the customer. At most the amount still due.")
    phone = fields.Char(
        "Customer Phone",
        help="Optional. 8 digits or 509XXXXXXXX. Speeds up NatCash and "
        "lets you send the instructions by WhatsApp or SMS.")
    ussd_available = fields.Boolean("USSD Available", readonly=True)
    channel = fields.Selection([
            ('link', "Link / QR Code"),
            ('ussd', "USSD"),
            ], "Channel", required=True, sort=False,
        states={'invisible': ~Eval('ussd_available', False)},
        help="Link / QR Code: the customer scans the code. USSD: the "
        "customer dials a code on the phone (no data needed).")
    transaction_id = fields.Integer("Transaction", readonly=True)

    @fields.depends('account', 'channel', 'ussd_available')
    def on_change_account(self):
        self.ussd_available = bool(self.account and self.account.ussd_enabled)
        if not self.ussd_available:
            self.channel = 'link'

    @classmethod
    def default_channel(cls):
        return 'link'


class PayResult(ModelView):
    "Mobile Payment Status"
    __name__ = 'mobile.payment.pay.result'

    transaction = fields.Many2One(
        'mobile.payment.transaction', "Transaction", readonly=True)
    order_ref = fields.Char("Order ID", readonly=True)
    state = fields.Selection(
        'get_states', "State", readonly=True)
    checkout_url = fields.Char(
        "Payment Link", readonly=True,
        help="Give this link to the customer (or open it on the terminal).")
    qr_code = fields.Binary(
        "QR Code", readonly=True,
        help="Show it to the customer: scanning it with the phone camera "
        "opens the payment page of the operator.")
    channel = fields.Selection([
            ('link', "Link / QR Code"),
            ('ussd', "USSD"),
            ], "Channel", readonly=True)
    ussd_code = fields.Char("USSD Code", readonly=True)
    ussd_uri = fields.Char("Dial Link", readonly=True)
    receipt = fields.Char(
        "Operator Receipt",
        states={'invisible': Eval('channel') != 'ussd',
            'readonly': Eval('state') != 'pending'},
        help="Number of the SMS the customer received from the operator. "
        "Type it then press Verify Payment.")
    share_whatsapp = fields.Char("Send by WhatsApp", readonly=True)
    share_sms = fields.Char("Send by SMS", readonly=True)
    message = fields.Text("Message", readonly=True)

    @classmethod
    def get_states(cls):
        Tx = Pool().get('mobile.payment.transaction')
        return Tx.fields_get(['state'])['state']['selection']


class Pay(Wizard):
    "Mobile Payment"
    __name__ = 'mobile.payment.pay'

    start = StateView(
        'mobile.payment.pay.start', 'mobile_payment.pay_start_view_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Request Payment", 'request', 'tryton-ok', default=True),
            ])
    request = StateTransition()
    result = StateView(
        'mobile.payment.pay.result', 'mobile_payment.pay_result_view_form', [
            Button("Close", 'end', 'tryton-close'),
            Button("Verify Payment", 'verify', 'tryton-refresh',
                default=True),
            ])
    verify = StateTransition()

    # Documents that can be paid: the module that adds one overrides these
    # two hooks and falls back on super() for the others.

    @classmethod
    def _target_start(cls, model, id):
        "Defaults of the start form for the document to pay"
        if model != 'sale.point.sale':
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_unknown_target', model=model))
        sale = Pool().get('sale.point.sale')(id)
        if sale.state != 'open':
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_sale_not_open', sale=sale.rec_name))
        return {
            'document': sale.rec_name,
            'company': sale.company.id,
            'currency': sale.currency.id,
            'amount': sale.amount_to_pay,
            }

    @classmethod
    def _target_values(cls, model, id):
        "Fields of the transaction that point to the document"
        return {'sale': id}

    def default_start(self, fields):
        Account = Pool().get('mobile.payment.account')
        context = Transaction().context
        model, id = context['active_model'], context['active_id']
        defaults = self._target_start(model, id)
        defaults.update(target_model=model, target_id=id)
        accounts = Account.search([('company', '=', defaults['company'])])
        if len(accounts) == 1:
            defaults['account'] = accounts[0].id
            defaults['ussd_available'] = accounts[0].ussd_enabled
        defaults['channel'] = 'link'
        return defaults

    def transition_request(self):
        pool = Pool()
        Tx = pool.get('mobile.payment.transaction')
        from .tools import normalize_phone
        start = self.start
        values = self._target_values(start.target_model, start.target_id)
        channel = start.channel or 'link'
        if channel == 'ussd' and not start.account.ussd_enabled:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_ussd_disabled',
                    account=start.account.rec_name))
        values.update(
            account=start.account.id, amount=start.amount,
            channel=channel, phone=normalize_phone(start.phone))
        tx, = Tx.create([values])
        Tx.request([tx])
        self.start.transaction_id = tx.id
        return 'result'

    def default_result(self, fields):
        Tx = Pool().get('mobile.payment.transaction')
        tx = Tx(self.start.transaction_id)
        return {
            'transaction': tx.id,
            'order_ref': tx.order_ref,
            'state': tx.state,
            'checkout_url': tx.checkout_url,
            'qr_code': tx.qr_code,
            'channel': tx.channel,
            'ussd_code': tx.ussd_code,
            'ussd_uri': tx.ussd_uri,
            'receipt': tx.receipt,
            'share_whatsapp': tx.share_whatsapp,
            'share_sms': tx.share_sms,
            'message': tx.message,
            }

    def transition_verify(self):
        Tx = Pool().get('mobile.payment.transaction')
        tx = Tx(self.start.transaction_id)
        if tx.channel == 'ussd':
            if tx.state in {'pending', 'expired'}:
                tx._confirm_ussd((self.result.receipt or '').strip())
        else:
            Tx.check([tx])
        return 'result'
