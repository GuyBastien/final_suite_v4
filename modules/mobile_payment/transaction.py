# This file is part of the Tryton mobile_payment module.
"""Mobile payment transactions: request, verify, confirm, expire."""
import datetime
import logging
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    ModelSQL, ModelView, Unique, Workflow, fields)
from trytond.modules.currency.fields import Monetary
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction as Tx, without_check_access

from .exceptions import MobilePaymentError

logger = logging.getLogger(__name__)
_ZERO = Decimal(0)


def _push(users, title, body, priority):
    from trytond import bus
    for user in users:
        try:
            bus.notify(title, body, priority, user=user)
        except Exception:  # never block the scheduler on a pop-up
            logger.warning("Cannot push notification", exc_info=True)


def _admins():
    pool = Pool()
    UserGroup = pool.get('res.user-res.group')
    group = pool.get('ir.model.data').get_id(
        'mobile_payment', 'group_mobile_payment_admin')
    return {ug.user.id for ug in UserGroup.search([('group', '=', group)])}


class Transaction(Workflow, ModelSQL, ModelView):
    "Mobile Payment Transaction"
    __name__ = 'mobile.payment.transaction'
    _rec_name = 'order_ref'

    company = fields.Many2One(
        'company.company', "Company", required=True, readonly=True)
    account = fields.Many2One(
        'mobile.payment.account', "Account", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'draft'},
        help="Account of the operator that receives the payment.")
    provider = fields.Selection(
        'get_providers', "Provider", readonly=True,
        help="Mobile money operator of the account.")
    sale = fields.Many2One(
        'sale.point.sale', "Sale", ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'draft',
            'invisible': ~Eval('sale')},
        help="Point of sale ticket paid by this transaction.")
    document = fields.Function(
        fields.Char("Document",
            help="The ticket, order or stay paid by this transaction."),
        'get_document')
    payment = fields.Many2One(
        'sale.point.payment', "Payment", readonly=True, ondelete='RESTRICT',
        states={'invisible': Eval('state') != 'succeeded'},
        help="Payment of the ticket created when the operator confirmed.")
    order_ref = fields.Char(
        "Order ID", readonly=True, required=True,
        help="Unique reference sent to the operator.")
    amount = Monetary(
        "Amount", currency='currency', digits='currency', required=True,
        domain=[('amount', '>', 0)],
        states={'readonly': Eval('state') != 'draft'},
        help="Amount asked to the customer.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency"),
        'on_change_with_currency')
    phone = fields.Char(
        "Customer Phone", states={'readonly': Eval('state') != 'draft'},
        help="Mobile number of the customer (8 digits or 509XXXXXXXX). "
        "Optional for MonCash, used by NatCash to skip the phone page.")
    checkout_url = fields.Char(
        "Payment Link", readonly=True,
        states={'invisible': (Eval('state') != 'pending')
            | (Eval('channel') == 'ussd')},
        help="Page of the operator where the customer confirms the payment. "
        "Open it on the customer phone or in a browser.")
    qr_code = fields.Function(fields.Binary(
            "QR Code", readonly=True,
            states={'invisible': (~Eval('checkout_url')
                    & (Eval('channel') != 'ussd'))
                | (Eval('state') != 'pending')},
            help="The customer scans it with the phone camera to open the "
            "payment page of the operator. Needs the python package segno."),
        'get_qr_code')
    channel = fields.Selection([
            ('link', "Link / QR Code"),
            ('ussd', "USSD"),
            ], "Channel", required=True, sort=False,
        states={'readonly': Eval('state') != 'draft'},
        help="Link / QR Code: the customer opens the page of the operator. "
        "USSD: the customer dials a code on the phone, then gives the "
        "receipt number sent by the operator.")
    receipt = fields.Char(
        "Operator Receipt",
        states={
            'invisible': Eval('channel') != 'ussd',
            'readonly': ~Eval('state').in_(['pending', 'expired'])},
        help="Transaction number that the customer received by SMS from the "
        "operator after the USSD payment. The operator confirms it; a "
        "receipt can be used only once.")
    manual = fields.Boolean(
        "Manual Confirmation", readonly=True,
        states={'invisible': ~Eval('manual', False)},
        help="Confirmed by an administrator without the operator's answer. "
        "Compare it with the operator statement.")
    confirmed_by = fields.Many2One(
        'res.user', "Confirmed by", readonly=True,
        states={'invisible': ~Eval('confirmed_by')},
        help="User who entered the receipt number.")
    ussd_code = fields.Function(
        fields.Char("USSD Code", states={
                'invisible': Eval('channel') != 'ussd'},
            help="Code the customer dials on the phone."), 'get_ussd')
    ussd_uri = fields.Function(
        fields.Char("Dial Link", states={
                'invisible': Eval('channel') != 'ussd'},
            help="Opens the dialler with the code. Useful when the "
            "customer's phone is the terminal."), 'get_ussd')
    instructions = fields.Function(
        fields.Text("Customer Instructions", states={
                'invisible': Eval('channel') != 'ussd'},
            help="Steps to read to the customer."), 'get_instructions')
    share_whatsapp = fields.Function(
        fields.Char("Send by WhatsApp", states={
                'invisible': (Eval('state') != 'pending') | ~Eval('phone')},
            help="Opens WhatsApp with the payment message ready for the "
            "customer's number."), 'get_share')
    share_sms = fields.Function(
        fields.Char("Send by SMS", states={
                'invisible': (Eval('state') != 'pending') | ~Eval('phone')},
            help="Opens the SMS application with the payment message ready "
            "for the customer's number."), 'get_share')
    provider_ref = fields.Char(
        "Operator Transaction", readonly=True,
        states={'invisible': Eval('state') != 'succeeded'},
        help="Transaction number given by the operator.")
    payer = fields.Char(
        "Payer", readonly=True,
        states={'invisible': Eval('state') != 'succeeded'}, help="Account of the customer who paid.")
    message = fields.Text(
        "Message", readonly=True, help="Last message of the operator.")
    expires_at = fields.DateTime(
        "Expires at", readonly=True,
        states={'invisible': Eval('state') != 'pending'},
        help="After this moment the request is expired by the scheduler.")
    orphan = fields.Boolean(
        "Needs Review", readonly=True,
        help="The operator confirmed a payment that cannot be booked on the "
        "ticket (ticket cancelled, already paid, or different amount). "
        "Refund or fix manually, then tick it off.")
    state = fields.Selection([
            ('draft', "Draft"),
            ('pending', "Pending"),
            ('succeeded', "Paid"),
            ('failed', "Failed"),
            ('expired', "Expired"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False,
        help="Pending: waiting for the customer. Paid: confirmed by the "
        "operator. Expired: not paid in time.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('order_unique', Unique(t, t.company, t.order_ref),
                'mobile_payment.msg_order_unique'),
            ('receipt_unique', Unique(t, t.provider, t.receipt),
                'mobile_payment.msg_receipt_unique'),
            ]
        cls._order.insert(0, ('id', 'DESC'))
        cls._transitions |= {
            ('draft', 'pending'),
            ('pending', 'succeeded'),
            ('pending', 'failed'),
            ('pending', 'expired'),
            ('expired', 'succeeded'),
            ('draft', 'cancelled'),
            ('pending', 'cancelled'),
            }
        cls._buttons.update({
                'request': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state']},
                'check': {
                    'invisible': ~Eval('state').in_(['pending', 'expired'])
                    | (Eval('channel') == 'ussd'),
                    'depends': ['state', 'channel']},
                'confirm_ussd': {
                    'invisible': (Eval('channel') != 'ussd')
                    | ~Eval('state').in_(['pending', 'expired']),
                    'icon': 'tryton-ok',
                    'depends': ['state', 'channel']},
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'pending']),
                    'depends': ['state']},
                'resolve': {
                    'invisible': ~Eval('orphan', False),
                    'depends': ['orphan']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('orphan', False), 'danger',
                    If(Eval('state') == 'succeeded', 'success',
                        If(Eval('state').in_(['failed', 'expired']), 'danger',
                            If(Eval('state') == 'pending', 'warning',
                                If(Eval('state') == 'cancelled',
                                    'muted', '')))))),
            ]

    @classmethod
    def get_providers(cls):
        return pool_account().get_providers()

    def get_qr_code(self, name=None):
        from .tools import qr_png
        if self.state == 'pending':
            if self.channel == 'ussd':
                return qr_png(self._ussd()[1])
            return qr_png(self.checkout_url)

    def _ussd(self):
        "(code, dial link) of a USSD payment, (None, None) otherwise."
        from .tools import tel_uri
        if self.channel != 'ussd' or not self.account:
            return None, None
        code = self.account.ussd_dial(self.amount)
        return code, tel_uri(code)

    def get_ussd(self, name):
        try:
            code, uri = self._ussd()
        except MobilePaymentError:
            return None
        return code if name == 'ussd_code' else uri

    def get_instructions(self, name=None):
        if self.channel != 'ussd' or not self.account:
            return None
        try:
            return self.account.ussd_steps(self.amount, self.currency.code)
        except MobilePaymentError:
            return None

    def get_share(self, name):
        from .tools import sms_url, whatsapp_url
        if self.state != 'pending' or not self.phone:
            return None
        if self.channel == 'ussd':
            text = '%s\n%s' % (self.get_instructions() or '', self.ussd_code
                or '')
        else:
            text = gettext(
                'mobile_payment.msg_share_link', amount=self.amount,
                currency=self.currency.code, provider=self.account.name,
                url=self.checkout_url or '')
        build = whatsapp_url if name == 'share_whatsapp' else sms_url
        return build(self.phone, text)

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def default_orphan(cls):
        return False

    @classmethod
    def default_channel(cls):
        return 'link'

    @classmethod
    def default_manual(cls):
        return False

    @fields.depends('account', 'channel')
    def on_change_account(self):
        self.provider = self.account.provider if self.account else None
        if self.account and not self.account.ussd_enabled:
            self.channel = 'link'

    @classmethod
    def default_company(cls):
        return Tx().context.get('company')

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        return self.company.currency if self.company else None

    # Documents that can be paid. A module that makes another document
    # payable adds its own Many2One field, lists it in target_fields() and
    # overrides the _target_* hooks and _book(); nothing else is copied.

    @classmethod
    def target_fields(cls):
        return ['sale']

    def get_target(self):
        for name in self.target_fields():
            target = getattr(self, name)
            if target:
                return target

    def get_document(self, name):
        target = self.get_target()
        return target.rec_name if target else ''

    def _target_open(self):
        "The document still accepts payments"
        return self.sale.state == 'open'

    def _target_due(self):
        "Amount that the document still has to receive"
        return self.sale.amount_to_pay

    @classmethod
    def _order_base(cls, values):
        "Return (readable prefix, domain of the other attempts)"
        sale = Pool().get('sale.point.sale')(values['sale'])
        return (sale.number or 'S%s' % sale.id, [('sale', '=', sale.id)])

    @classmethod
    def validate(cls, transactions):
        super().validate(transactions)
        for tx in transactions:
            targets = [n for n in cls.target_fields() if getattr(tx, n)]
            if len(targets) != 1:
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_one_target',
                        transaction=tx.rec_name))

    @fields.depends('sale', 'amount', '_parent_sale.amount_to_pay')
    def on_change_sale(self):
        if self.sale and not self.amount:
            self.amount = self.sale.amount_to_pay

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Account = pool.get('mobile.payment.account')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if values.get('account'):
                account = Account(values['account'])
                values.setdefault('provider', account.provider)
                values.setdefault('company', account.company.id)
            if not values.get('order_ref') and any(
                    values.get(n) for n in cls.target_fields()):
                base, domain = cls._order_base(values)
                values['order_ref'] = cls._new_order_ref(
                    base, domain, values.get('company'))
        return super().create(vlist)

    @classmethod
    def copy(cls, transactions, default=None):
        default = {} if default is None else default.copy()
        for field in ('payment', 'order_ref', 'checkout_url', 'provider_ref',
                'payer', 'message', 'expires_at', 'receipt', 'confirmed_by'):
            default.setdefault(field, None)
        default['state'] = 'draft'
        default['orphan'] = False
        default['manual'] = False
        return super().copy(transactions, default=default)

    @classmethod
    def _new_order_ref(cls, base, domain, company):
        "Unique and readable: <document number or id>-<attempt>."
        attempt = cls.search_count(domain) + 1
        order_ref = '%s-%s' % (base, attempt)
        while cls.search_count([
                    ('company', '=', company),
                    ('order_ref', '=', order_ref)]):
            attempt += 1
            order_ref = '%s-%s' % (base, attempt)
        return order_ref

    @classmethod
    def delete(cls, transactions):
        for tx in transactions:
            if tx.state not in {'draft', 'cancelled'}:
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_delete_state',
                        transaction=tx.rec_name))
        super().delete(transactions)

    # Workflow

    @classmethod
    @ModelView.button
    @Workflow.transition('pending')
    def request(cls, transactions):
        now = datetime.datetime.now()
        for tx in transactions:
            if not tx._target_open():
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_sale_not_open',
                        sale=tx.document))
            if tx.amount > tx._target_due():
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_amount_too_high',
                        amount=tx.amount, due=tx._target_due()))
            if tx.channel == 'ussd':
                account = tx.account
                if not account.ussd_enabled:
                    raise MobilePaymentError(gettext(
                            'mobile_payment.msg_ussd_disabled',
                            account=account.rec_name))
                tx.message = account.ussd_steps(tx.amount, tx.currency.code)
                tx.expires_at = now + datetime.timedelta(
                    minutes=account.expiry_minutes)
                tx.save()
                continue
            result = tx.account.provider_call('create', tx)
            tx.checkout_url = result.get('checkout_url')
            tx.provider_ref = result.get('provider_ref')
            tx.message = result.get('message')
            tx.expires_at = result.get('expires_at') or (
                now + datetime.timedelta(minutes=tx.account.expiry_minutes))
            tx.save()

    @classmethod
    @ModelView.button
    def check(cls, transactions):
        "Ask the operator and apply the answer."
        for tx in transactions:
            if tx.state not in {'pending', 'expired'}:
                continue
            if tx.channel == 'ussd':
                if tx.receipt:
                    tx._confirm_ussd()
                continue
            tx._apply(tx.account.provider_call('check', tx))

    def _apply(self, result):
        """Apply a provider answer: status pending/succeeded/failed."""
        cls = self.__class__
        status = result.get('status', 'pending')
        values = {k: result[k] for k in ('provider_ref', 'payer', 'message')
            if result.get(k)}
        if values:
            cls.write([self], values)
        if status == 'succeeded':
            amount = result.get('amount')
            ok = (self._target_open()
                and self.amount <= self._target_due()
                and (amount is None
                    or Decimal(str(amount)) == Decimal(self.amount)))
            if ok:
                self._book()
            else:
                cls.write([self], {
                        'orphan': True,
                        'message': gettext(
                            'mobile_payment.msg_orphan_message',
                            message=result.get('message') or '')})
                cls.succeed([self])
                cls(self.id).notify_orphan()
        elif status == 'failed':
            cls.fail([self])
            cls(self.id).notify_result()

    def _book(self):
        "Book the confirmed payment on the document and finish it."
        if not self.sale:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_no_booking_rule',
                    transaction=self.rec_name))
        self._book_sale()

    def _book_sale(self):
        "Create the payment on the ticket and finish it."
        pool = Pool()
        Payment = pool.get('sale.point.payment')
        Sale = pool.get('sale.point.sale')
        with Tx().set_context(mobile_confirm=True):
            payment, = Payment.create([{
                        'sale': self.sale.id,
                        'method': self.account.method.id,
                        'amount': self.amount}])
        self.__class__.write([self], {'payment': payment.id})
        self.__class__.succeed([self])
        sale = Sale(self.sale.id)
        if sale.state == 'open' and not sale.amount_to_pay:
            Sale.process([sale])
        self.__class__(self.id).notify_result()

    @classmethod
    @Workflow.transition('succeeded')
    def succeed(cls, transactions):
        pass

    @classmethod
    @Workflow.transition('failed')
    def fail(cls, transactions):
        pass

    @classmethod
    @Workflow.transition('expired')
    def expire(cls, transactions):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, transactions):
        pass

    @classmethod
    @ModelView.button
    def confirm_ussd(cls, transactions):
        "Verify the receipt number typed from the customer's SMS."
        for tx in transactions:
            tx._confirm_ussd()

    def _confirm_ussd(self, receipt=None):
        """Book a USSD payment once the operator confirms its receipt.

        1. the receipt is mandatory and can be used only once;
        2. if the provider can look the receipt up, its answer decides;
        3. otherwise only an administrator may confirm by hand, when the
           account allows it, and the payment is flagged for review."""
        pool = Pool()
        User = pool.get('res.user')
        cls = self.__class__
        if self.channel != 'ussd' or self.state not in {'pending', 'expired'}:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_not_ussd', transaction=self.rec_name))
        receipt = (receipt or self.receipt or '').strip()
        if not receipt:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_receipt_required'))
        with without_check_access():
            used = cls.search([
                    ('provider', '=', self.provider),
                    ('id', '!=', self.id), ['OR',
                        ('receipt', '=', receipt),
                        ('provider_ref', '=', receipt)]], limit=1)
        if used:
            raise MobilePaymentError(gettext(
                    'mobile_payment.msg_receipt_used',
                    receipt=receipt, transaction=used[0].order_ref))
        account = self.account
        values = {'receipt': receipt, 'confirmed_by': Tx().user}
        manual = False
        if account.provider_supports('lookup'):
            result = account.provider_call('lookup', self)
            status = result.get('status')
            reference = result.get('reference')
            if reference and reference != self.order_ref:
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_receipt_other_order',
                        receipt=receipt, reference=reference))
            if status in {'pending', 'failed'}:
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_receipt_rejected',
                        receipt=receipt, message=result.get('message') or ''))
        else:
            result = None
        if result is None or result.get('status') == 'unknown':
            if not account.allow_manual:
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_manual_not_allowed',
                        receipt=receipt, account=account.rec_name))
            group = pool.get('ir.model.data').get_id(
                'mobile_payment', 'group_mobile_payment_admin')
            if group not in User.get_groups():
                raise MobilePaymentError(gettext(
                        'mobile_payment.msg_manual_admin_only',
                        receipt=receipt))
            manual = True
            result = {
                'status': 'succeeded', 'provider_ref': receipt,
                'message': gettext('mobile_payment.msg_manual_message')}
        else:
            result.setdefault('provider_ref', receipt)
        cls.write([self], values)
        self._apply(result)
        tx = cls(self.id)
        if manual and tx.state == 'succeeded':
            cls.write([tx], {'manual': True, 'orphan': True})
            cls(self.id).notify_manual()

    @classmethod
    @ModelView.button
    def resolve(cls, transactions):
        "The manager handled the discrepancy."
        cls.write(transactions, {'orphan': False})

    # Notifications

    def _cashier(self):
        user = self.create_uid
        return {user.id} if user else set()

    def _broadcast(self, event, title, body, priority):
        "Send the event to the push targets of the company."
        Push = Pool().get('mobile.payment.push')
        try:
            Push.enqueue(self.company, event, title, body, priority, {
                    'order': self.order_ref, 'amount': str(self.amount),
                    'currency': self.currency.code,
                    'provider': self.provider, 'state': self.state,
                    'channel': self.channel})
        except Exception:  # push is a courtesy, never block a payment
            logger.warning("Cannot queue push", exc_info=True)

    def notify_manual(self):
        title = gettext('mobile_payment.msg_notify_manual_title')
        body = gettext(
            'mobile_payment.msg_notify_manual', order=self.order_ref,
            amount=self.amount, provider=self.account.name,
            receipt=self.receipt)
        _push(_admins() | self._cashier(), title, body, 4)
        self._broadcast('manual', title, body, 4)

    def notify_result(self):
        if self.state == 'succeeded':
            title = gettext('mobile_payment.msg_notify_paid_title')
            body = gettext(
                'mobile_payment.msg_notify_paid', order=self.order_ref,
                amount=self.amount, provider=self.account.name)
            priority = 2
        else:
            title = gettext('mobile_payment.msg_notify_failed_title')
            body = gettext(
                'mobile_payment.msg_notify_failed', order=self.order_ref,
                provider=self.account.name)
            priority = 4
        _push(self._cashier(), title, body, priority)
        self._broadcast(
            'succeeded' if self.state == 'succeeded' else 'failed',
            title, body, priority)

    def notify_orphan(self):
        title = gettext('mobile_payment.msg_notify_orphan_title')
        body = gettext(
            'mobile_payment.msg_notify_orphan', order=self.order_ref,
            amount=self.amount, provider=self.account.name)
        _push(_admins() | self._cashier(), title, body, 4)
        self._broadcast('orphan', title, body, 5)

    @classmethod
    def reconcile(cls):
        """Scheduled action: verify pending requests and expire old ones.

        Return the number of transactions whose state changed."""
        now = datetime.datetime.now()
        changed = 0
        with without_check_access():
            pending = cls.search(
                [('state', '=', 'pending')], order=[('id', 'ASC')], limit=200)
            for tx in pending:
                before = tx.state
                try:
                    if tx.channel == 'link':
                        tx._apply(tx.account.provider_call('check', tx))
                except MobilePaymentError:
                    logger.warning(
                        "Cannot verify %s", tx.order_ref, exc_info=True)
                tx = cls(tx.id)
                if (tx.state == 'pending' and tx.expires_at
                        and tx.expires_at < now):
                    cls.expire([tx])
                    tx.notify_result_expired()
                    tx = cls(tx.id)
                changed += tx.state != before
        return changed

    @classmethod
    def handle_return(cls, provider, params):
        """Return URL: verify the matching pending transactions.

        The parameters are only used to find which transaction to verify
        (and by the provider to reject forged callbacks); the verdict always
        comes from the operator. Return the number of paid transactions."""
        paid = 0
        with without_check_access():
            domain = [('state', '=', 'pending'), ('provider', '=', provider),
                ('channel', '=', 'link')]
            order_ref, error = None, None
            accounts = {tx.account.id: tx.account for tx in cls.search(domain)}
            for account in accounts.values():
                try:
                    order_ref = account.provider_call_params(params)
                except MobilePaymentError as e:  # forged or foreign callback
                    error = e
                    continue
                error = None
                break
            if error is not None:
                raise error
            if order_ref:
                domain.append(('order_ref', '=', order_ref))
            for tx in cls.search(domain, limit=50):
                try:
                    tx._apply(tx.account.provider_call('check', tx))
                except MobilePaymentError:
                    logger.warning(
                        "Cannot verify %s", tx.order_ref, exc_info=True)
                paid += cls(tx.id).state == 'succeeded'
        return paid

    def notify_result_expired(self):
        title = gettext('mobile_payment.msg_notify_expired_title')
        body = gettext(
            'mobile_payment.msg_notify_expired', order=self.order_ref,
            provider=self.account.name)
        _push(self._cashier(), title, body, 3)
        self._broadcast('expired', title, body, 3)


def pool_account():
    return Pool().get('mobile.payment.account')


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('mobile.payment.transaction|reconcile',
                "Verify Mobile Payments"))
