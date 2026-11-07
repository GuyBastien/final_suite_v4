# This file is part of the Tryton fuel_station module.
"""Dispensing authorizations.

Tryton only *requests* and *records*: the certified controller of the
station decides physically whether a pump may dispense. Tryton never talks
to the pump control circuits; a middleware validated by the equipment
manufacturer and the authorized installer exchanges the authorizations with
the controller and calls the ``middleware_*`` methods below.
"""
import datetime
import math
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.modules.product import price_digits
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.rpc import RPC
from trytond.transaction import Transaction, without_check_access

from .exceptions import AuthorizationError

_ZERO = Decimal(0)
LIVE_STATES = ['approved', 'transmitted', 'confirmed', 'cancel_requested']
EVENTS = [
    ('requested', "Requested"),
    ('approved', "Approved"),
    ('rejected', "Rejected"),
    ('transmitted', "Transmitted to the controller"),
    ('confirmed', "Confirmed by the controller"),
    ('refused', "Refused by the controller"),
    ('completed', "Completed"),
    ('discrepancy', "Discrepancy"),
    ('expired', "Expired"),
    ('cancelled', "Cancelled"),
    ('cancel_requested', "Cancellation requested"),
    ('resolved', "Discrepancy resolved"),
    ('info', "Information"),
    ]


def _now():
    return datetime.datetime.now()


class Station(metaclass=PoolMeta):
    __name__ = 'fuel.station'

    authorization_control = fields.Boolean(
        "Authorization Control",
        help="Pump dispensing requires an authorization requested in "
        "Tryton, approved by a manager and confirmed by the controller.")
    authorization_ttl = fields.Integer(
        "Authorization Validity (min)",
        domain=[('authorization_ttl', '>', 0)],
        states={'invisible': ~Eval('authorization_control', False)},
        help="An approved authorization that the controller has not "
        "confirmed within this delay expires.")
    heartbeat_timeout = fields.Integer(
        "Communication Timeout (s)",
        domain=[('heartbeat_timeout', '>', 0)],
        states={'invisible': ~Eval('authorization_control', False)},
        help="Without a signal from the middleware within this delay, "
        "the communication is considered lost and no authorization can "
        "be requested, approved or transmitted.")
    last_heartbeat = fields.DateTime("Last Middleware Signal", readonly=True,
        help="Last time the middleware signaled that it is alive.")
    communication_blocked = fields.Boolean(
        "Block Communication",
        states={'invisible': ~Eval('authorization_control', False)},
        help="Manual block: nothing is authorized while it is checked.")
    communication_ok = fields.Function(
        fields.Boolean("Communication OK",
        help="Uncheck when the link with the controller is lost: new authorizations are then blocked."), 'get_communication_ok')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.__rpc__.update({
                'heartbeat': RPC(readonly=False),
                })

    @classmethod
    def default_authorization_control(cls):
        return False

    @classmethod
    def default_authorization_ttl(cls):
        return 5

    @classmethod
    def default_heartbeat_timeout(cls):
        return 120

    @classmethod
    def default_communication_blocked(cls):
        return False

    def get_communication_ok(self, name):
        if self.communication_blocked or not self.last_heartbeat:
            return False
        delta = _now() - self.last_heartbeat
        return delta.total_seconds() <= self.heartbeat_timeout

    @classmethod
    def heartbeat(cls, code, company=None):
        "Called by the middleware to prove that the communication is alive"
        Authorization = Pool().get('fuel.authorization')
        Authorization._require_middleware()
        station = Authorization._station(code, company)
        with without_check_access():
            cls.write([station], {'last_heartbeat': _now()})
        return True


class Authorization(Workflow, ModelSQL, ModelView):
    "Fuel Dispensing Authorization"
    __name__ = 'fuel.authorization'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'pending'},
        help="Company that owns the record.")
    station = fields.Many2One(
        'fuel.station', "Station", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'pending'},
        help="Station concerned.")
    pump = fields.Many2One(
        'fuel.pump', "Pump", required=True,
        domain=[('station', '=', Eval('station', -1))],
        states={'readonly': Eval('state') != 'pending'},
        help="Pump concerned.")
    nozzle = fields.Many2One(
        'fuel.nozzle', "Nozzle", required=True,
        domain=[('pump', '=', Eval('pump', -1))],
        states={'readonly': Eval('state') != 'pending'},
        help="The nozzle imposes the fuel and the tank.")
    product = fields.Function(
        fields.Many2One('product.product', "Fuel",
            context={'company': Eval('company', -1)},
        help="Product concerned."),
        'on_change_with_product')
    unit = fields.Function(
        fields.Many2One('product.uom', "Unit",
        help="Unit of measure."), 'on_change_with_unit')
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    party = fields.Many2One(
        'party.party', "Customer",
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'pending'},
        help="Customer or contact concerned.")
    vehicle = fields.Char(
        "Vehicle", states={'readonly': Eval('state') != 'pending'},
        help="Plate number or identification of the vehicle.")
    limit_type = fields.Selection([
            ('quantity', "Maximum Quantity"),
            ('amount', "Maximum Amount"),
            ], "Limit", required=True,
        states={'readonly': Eval('state') != 'pending'},
        help="Whether the limit is a quantity or an amount.")
    max_quantity = fields.Float(
        "Maximum Quantity", digits=(16, 3),
        domain=[('max_quantity', '>', 0)],
        states={'readonly': (Eval('state') != 'pending')
            | (Eval('limit_type') != 'quantity'),
            'required': Eval('limit_type') == 'quantity'},
        help="Maximum volume (gallons) the pump may dispense.")
    max_amount = fields.Numeric(
        "Maximum Amount", digits='currency',
        domain=[('max_amount', '>', 0)],
        states={'readonly': (Eval('state') != 'pending')
            | (Eval('limit_type') != 'amount'),
            'required': Eval('limit_type') == 'amount'},
        help="Maximum amount the pump may dispense.")
    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits, readonly=True,
        help="Price of one unit before discount.")
    state = fields.Selection([
            ('pending', "Pending"),
            ('approved', "Approved"),
            ('transmitted', "Transmitted"),
            ('confirmed', "Confirmed"),
            ('completed', "Completed"),
            ('rejected', "Rejected"),
            ('refused', "Refused"),
            ('cancel_requested', "Cancellation Requested"),
            ('cancelled', "Cancelled"),
            ('expired', "Expired"),
            ('discrepancy', "Discrepancy"),
            ('resolved', "Resolved"),
            ], "State", readonly=True, required=True,
        help="Current step of the workflow.")
    # Audit
    requested_by = fields.Many2One('res.user', "Requested by", readonly=True,
        help="User who made the request.")
    requested_at = fields.DateTime("Requested at", readonly=True,
        help="Date and time of the request.")
    approved_by = fields.Many2One('res.user', "Approved by", readonly=True,
        help="User who approved the request.")
    approved_at = fields.DateTime("Approved at", readonly=True,
        help="Date and time of the approval.")
    expires_at = fields.DateTime("Expires at", readonly=True,
        help="After this time the authorization can no longer be used.")
    # Controller data
    controller_ref = fields.Char("Controller Reference", readonly=True,
        help="Reference given by the pump controller for this authorization.")
    transaction_ref = fields.Char("Transaction Reference", readonly=True,
        help="Transaction number given by the controller.")
    dispensed_quantity = fields.Float(
        "Dispensed Quantity", digits=(16, 3), readonly=True,
        help="Quantity reported by the controller for the distribution.")
    dispensed_amount = fields.Numeric(
        "Dispensed Amount", digits='currency', readonly=True,
        help="Amount reported by the controller for the distribution.")
    shift = fields.Many2One('fuel.shift', "Shift", readonly=True,
        help="Shift concerned.")
    move = fields.Many2One('stock.move', "Stock Move", readonly=True,
        help="Stock move generated by this record.")
    notes = fields.Text("Notes",
        help="Free text for internal use.")
    logs = fields.One2Many(
        'fuel.authorization.log', 'authorization', "Audit Log",
        readonly=True,
        help="Audit trail of every change of state.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('id', 'DESC'))
        cls._transitions |= {
            ('pending', 'approved'),
            ('pending', 'rejected'),
            ('pending', 'cancelled'),
            ('approved', 'cancelled'),
            ('approved', 'transmitted'),
            ('approved', 'expired'),
            ('transmitted', 'confirmed'),
            ('transmitted', 'expired'),
            ('transmitted', 'refused'),
            ('transmitted', 'cancel_requested'),
            ('confirmed', 'cancel_requested'),
            ('cancel_requested', 'cancelled'),
            ('confirmed', 'completed'),
            ('transmitted', 'discrepancy'),
            ('confirmed', 'discrepancy'),
            ('expired', 'discrepancy'),
            ('cancel_requested', 'discrepancy'),
            ('cancelled', 'discrepancy'),
            ('discrepancy', 'resolved'),
            }
        cls._buttons.update({
                'approve': {
                    'invisible': Eval('state') != 'pending',
                    'depends': ['state']},
                'reject': {
                    'invisible': Eval('state') != 'pending',
                    'depends': ['state']},
                'cancel': {
                    'invisible': ~Eval('state').in_([
                            'pending', 'approved', 'transmitted',
                            'confirmed']),
                    'depends': ['state']},
                'resolve': {
                    'invisible': Eval('state') != 'discrepancy',
                    'depends': ['state']},
                })
        cls.__rpc__.update({
                'middleware_pending': RPC(readonly=False),
                'middleware_transmitted': RPC(readonly=False),
                'middleware_confirmed': RPC(readonly=False),
                'middleware_refused': RPC(readonly=False),
                'middleware_result': RPC(readonly=False),
                'middleware_cancel_ack': RPC(readonly=False),
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state').in_(['discrepancy', 'refused']), 'danger',
                    If(Eval('state').in_(['pending', 'cancel_requested']),
                        'warning',
                        If(Eval('state') == 'completed', 'success',
                            If(Eval('state').in_([
                                        'cancelled', 'rejected', 'expired',
                                        'resolved']), 'muted', ''))))),
            ]

    # Defaults / on_change

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_state(cls):
        return 'pending'

    @classmethod
    def default_limit_type(cls):
        return 'quantity'

    @classmethod
    def default_station(cls):
        Station = Pool().get('fuel.station')
        stations = Station.search([
                ('company', '=', cls.default_company()),
                ('authorization_control', '=', True)], limit=2)
        if len(stations) == 1:
            return stations[0].id

    @fields.depends('nozzle')
    def on_change_with_product(self, name=None):
        if self.nozzle and self.nozzle.tank:
            return self.nozzle.tank.product.id

    @fields.depends('nozzle')
    def on_change_with_unit(self, name=None):
        if self.nozzle and self.nozzle.tank:
            return self.nozzle.tank.product.default_uom.id

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @fields.depends('pump', 'nozzle')
    def on_change_pump(self):
        if self.pump:
            nozzles = [n for n in self.pump.nozzles if n.active]
            if len(nozzles) == 1:
                self.nozzle = nozzles[0]
            elif self.nozzle and self.nozzle.pump != self.pump:
                self.nozzle = None

    def _price(self):
        Price = Pool().get('fuel.price')
        return Price.get_price(
            self.station, self.nozzle.tank.product,
            Pool().get('ir.date').today())

    def _compute_limits(self):
        "Fill the limit that the operator did not enter"
        price = self._price()
        self.unit_price = price
        currency = self.company.currency
        if self.limit_type == 'quantity':
            self.max_amount = currency.round(
                Decimal(str(round(self.max_quantity, 6))) * price)
        else:
            self.max_quantity = math.floor(
                float(self.max_amount / price) * 1000) / 1000.

    @fields.depends('nozzle', 'station', 'company', 'limit_type',
        'max_quantity', 'max_amount', 'unit_price')
    def on_change_limit_type(self):
        self._try_limits()

    @fields.depends('nozzle', 'station', 'company', 'limit_type',
        'max_quantity', 'max_amount', 'unit_price')
    def on_change_max_quantity(self):
        if self.limit_type == 'quantity':
            self._try_limits()

    @fields.depends('nozzle', 'station', 'company', 'limit_type',
        'max_quantity', 'max_amount', 'unit_price')
    def on_change_max_amount(self):
        if self.limit_type == 'amount':
            self._try_limits()

    def _try_limits(self):
        if not (self.nozzle and self.station and self.company):
            return
        value = (self.max_quantity if self.limit_type == 'quantity'
            else self.max_amount)
        if not value:
            return
        try:
            self._compute_limits()
        except Exception:
            pass

    # CRUD

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        Station = pool.get('fuel.station')
        sequence = Sequence(ModelData.get_id(
                'fuel_station', 'sequence_authorization'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            station = Station(values['station'])
            cls._check_station_open(station)
            if not values.get('number'):
                values['number'] = sequence.get()
            values.setdefault('requested_by', Transaction().user)
            values.setdefault('requested_at', _now())
        auths = super().create(vlist)
        for auth in auths:
            auth._compute_limits()
            super(Authorization, cls).write([auth], {
                    'unit_price': auth.unit_price,
                    'max_quantity': auth.max_quantity,
                    'max_amount': auth.max_amount})
        auths = cls.browse([a.id for a in auths])
        cls._log(auths, 'requested', gettext(
                'fuel_station.msg_authorization_log_requested'))
        return auths

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for auths, values in zip(actions, actions):
            if {'pump', 'nozzle', 'limit_type', 'max_quantity',
                    'max_amount', 'station', 'party', 'vehicle'} & set(values):
                for auth in auths:
                    if auth.state != 'pending':
                        raise AuthorizationError(gettext(
                                'fuel_station.msg_authorization_locked',
                                authorization=auth.rec_name))
        super().write(*args)
        actions = iter(args)
        for auths, values in zip(actions, actions):
            if {'nozzle', 'limit_type', 'max_quantity', 'max_amount'
                    } & set(values):
                for auth in cls.browse([a.id for a in auths]):
                    auth._compute_limits()
                    super(Authorization, cls).write([auth], {
                            'unit_price': auth.unit_price,
                            'max_quantity': auth.max_quantity,
                            'max_amount': auth.max_amount})

    @classmethod
    def delete(cls, auths):
        raise AuthorizationError(gettext(
                'fuel_station.msg_authorization_delete'))

    @classmethod
    def copy(cls, auths, default=None):
        default = {} if default is None else default.copy()
        for name in ('number', 'approved_by', 'approved_at', 'expires_at',
                'controller_ref', 'transaction_ref', 'dispensed_quantity',
                'dispensed_amount', 'shift', 'move', 'logs'):
            default.setdefault(name, None)
        default.setdefault('state', 'pending')
        return super().copy(auths, default=default)

    @classmethod
    def validate(cls, auths):
        super().validate(auths)
        for auth in auths:
            if auth.nozzle.pump != auth.pump or auth.pump.station != auth.station:
                raise AuthorizationError(gettext(
                        'fuel_station.msg_authorization_nozzle',
                        nozzle=auth.nozzle.rec_name))

    # Helpers

    @classmethod
    def _check_station_open(cls, station):
        if not station.authorization_control:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_not_enabled',
                    station=station.rec_name))
        if not station.communication_ok:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_comm_lost',
                    station=station.rec_name))

    @classmethod
    def _log(cls, auths, event, note=''):
        Log = Pool().get('fuel.authorization.log')
        with without_check_access():
            Log.create([{
                        'authorization': a.id, 'event': event,
                        'user': Transaction().user, 'timestamp': _now(),
                        'note': note,
                        'state': a.state} for a in auths])

    @classmethod
    def _require_group(cls, xml_id, message):
        pool = Pool()
        User = pool.get('res.user')
        ModelData = pool.get('ir.model.data')
        user = Transaction().user
        if user == 0:
            return
        group = ModelData.get_id('fuel_station', xml_id)
        if group not in User.get_groups():
            raise AuthorizationError(gettext(message))

    @classmethod
    def _require_manager(cls):
        cls._require_group(
            'group_fuel_manager', 'fuel_station.msg_authorization_not_manager')

    @classmethod
    def _require_middleware(cls):
        cls._require_group(
            'group_fuel_middleware',
            'fuel_station.msg_authorization_not_middleware')

    @classmethod
    def _station(cls, code, company=None):
        Station = Pool().get('fuel.station')
        domain = [('code', '=', code)]
        company = company or Transaction().context.get('company')
        if company:
            domain.append(('company', '=', company))
        stations = Station.search(domain, limit=1)
        if not stations:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_unknown_station',
                    station=code))
        return stations[0]

    @classmethod
    def _by_number(cls, number):
        auths = cls.search([('number', '=', number)], limit=1)
        if not auths:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_unknown',
                    number=number))
        return auths[0]

    def _expect(self, states):
        if self.state not in states:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_state',
                    authorization=self.rec_name,
                    state=dict(self._fields['state'].selection)[self.state]))

    @classmethod
    def _set(cls, auth, state, **values):
        values['state'] = state
        with without_check_access():
            super(Authorization, cls).write([auth], values)

    # Buttons

    @classmethod
    @ModelView.button
    def approve(cls, auths):
        cls._require_manager()
        user = Transaction().user
        for auth in auths:
            auth._expect(['pending'])
            cls._check_station_open(auth.station)
            if auth.requested_by and auth.requested_by.id == user and user:
                raise AuthorizationError(gettext(
                        'fuel_station.msg_authorization_same_user',
                        authorization=auth.rec_name))
            busy = cls.search([
                    ('pump', '=', auth.pump.id),
                    ('state', 'in', LIVE_STATES),
                    ('id', '!=', auth.id)], limit=1)
            if busy:
                raise AuthorizationError(gettext(
                        'fuel_station.msg_authorization_pump_busy',
                        pump=auth.pump.rec_name,
                        authorization=busy[0].rec_name))
            cls._set(auth, 'approved', approved_by=user,
                approved_at=_now(),
                expires_at=_now() + datetime.timedelta(
                    minutes=auth.station.authorization_ttl))
            cls._log([cls(auth.id)], 'approved')

    @classmethod
    @ModelView.button
    def reject(cls, auths):
        cls._require_manager()
        for auth in auths:
            auth._expect(['pending'])
            cls._set(auth, 'rejected', approved_by=Transaction().user,
                approved_at=_now())
            cls._log([cls(auth.id)], 'rejected')

    @classmethod
    @ModelView.button
    def cancel(cls, auths):
        user = Transaction().user
        for auth in auths:
            auth._expect(['pending', 'approved', 'transmitted', 'confirmed'])
            if auth.state in {'pending', 'approved'}:
                if not (auth.requested_by and auth.requested_by.id == user):
                    cls._require_manager()
                cls._set(auth, 'cancelled')
                cls._log([cls(auth.id)], 'cancelled')
            else:
                # Already given to the controller: only a manager can ask,
                # and the controller must acknowledge.
                cls._require_manager()
                cls._set(auth, 'cancel_requested')
                cls._log([cls(auth.id)], 'cancel_requested', gettext(
                        'fuel_station.msg_authorization_log_cancel'))

    @classmethod
    @ModelView.button
    def resolve(cls, auths):
        cls._require_manager()
        for auth in auths:
            auth._expect(['discrepancy'])
            cls._set(auth, 'resolved')
            cls._log([cls(auth.id)], 'resolved')

    # Expiration

    @classmethod
    def expire(cls):
        "Cron: expire the approved authorizations never confirmed"
        auths = cls.search([
                ('state', 'in', ['approved', 'transmitted']),
                ('expires_at', '<', _now())])
        for auth in auths:
            cls._set(auth, 'expired')
            cls._log([cls(auth.id)], 'expired')
        return len(auths)

    # Middleware interface (called with RPC by a technical user)

    @classmethod
    def middleware_pending(cls, station_code, company=None):
        """Approved authorizations to give to the controller.
        Also counts as a signal of life of the middleware."""
        cls._require_middleware()
        station = cls._station(station_code, company)
        Station = Pool().get('fuel.station')
        with without_check_access():
            Station.write([station], {'last_heartbeat': _now()})
        station = Station(station.id)
        if not station.communication_ok:
            return []
        cls.expire()
        result = []
        for auth in cls.search([
                    ('station', '=', station.id), ('state', '=', 'approved')],
                order=[('id', 'ASC')]):
            result.append({
                    'number': auth.number,
                    'pump': auth.pump.code,
                    'nozzle': auth.nozzle.number,
                    'product': auth.nozzle.tank.product.rec_name,
                    'max_quantity': auth.max_quantity,
                    'max_amount': str(auth.max_amount),
                    'unit_price': str(auth.unit_price),
                    'vehicle': auth.vehicle,
                    'expires_at': auth.expires_at.isoformat(),
                    })
        return result

    @classmethod
    def middleware_transmitted(cls, number):
        cls._require_middleware()
        auth = cls._by_number(number)
        auth._expect(['approved'])
        if auth.expires_at < _now():
            cls.expire()
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_expired',
                    authorization=auth.rec_name))
        if not auth.station.communication_ok:
            raise AuthorizationError(gettext(
                    'fuel_station.msg_authorization_comm_lost',
                    station=auth.station.rec_name))
        cls._set(auth, 'transmitted')
        cls._log([cls(auth.id)], 'transmitted')
        return True

    @classmethod
    def middleware_confirmed(cls, number, controller_ref=None):
        cls._require_middleware()
        auth = cls._by_number(number)
        if auth.state in {'expired', 'cancelled', 'cancel_requested'}:
            # The controller authorized something Tryton no longer allows
            cls._set(auth, 'discrepancy')
            cls._log([cls(auth.id)], 'discrepancy', gettext(
                    'fuel_station.msg_authorization_log_late'))
            return False
        auth._expect(['transmitted'])
        cls._set(auth, 'confirmed', controller_ref=controller_ref)
        cls._log([cls(auth.id)], 'confirmed', controller_ref or '')
        return True

    @classmethod
    def middleware_refused(cls, number, reason=''):
        cls._require_middleware()
        auth = cls._by_number(number)
        auth._expect(['transmitted'])
        cls._set(auth, 'refused')
        cls._log([cls(auth.id)], 'refused', reason or '')
        return True

    @classmethod
    def middleware_cancel_ack(cls, number):
        cls._require_middleware()
        auth = cls._by_number(number)
        auth._expect(['cancel_requested'])
        cls._set(auth, 'cancelled')
        cls._log([cls(auth.id)], 'cancelled', gettext(
                'fuel_station.msg_authorization_log_ack'))
        return True

    @classmethod
    def middleware_result(cls, number, quantity, amount,
            transaction_ref=None):
        """Distribution data sent back by the controller. Compared with
        the authorization: any difference is reported, never ignored."""
        cls._require_middleware()
        auth = cls._by_number(number)
        quantity = float(quantity)
        amount = Decimal(str(amount))
        if transaction_ref:
            twin = cls.search([
                    ('transaction_ref', '=', transaction_ref),
                    ('id', '!=', auth.id)], limit=1)
            if twin:
                raise AuthorizationError(gettext(
                        'fuel_station.msg_authorization_transaction',
                        transaction=transaction_ref))
        values = {
            'dispensed_quantity': quantity, 'dispensed_amount': amount,
            'transaction_ref': transaction_ref}
        problems = []
        if auth.state != 'confirmed':
            problems.append(gettext(
                    'fuel_station.msg_authorization_log_state',
                    state=dict(cls.state.selection)[auth.state]))
        if quantity > auth.max_quantity + 1e-6:
            problems.append(gettext(
                    'fuel_station.msg_authorization_log_quantity',
                    dispensed=quantity, limit=auth.max_quantity))
        if amount > auth.max_amount + auth.company.currency.rounding:
            problems.append(gettext(
                    'fuel_station.msg_authorization_log_amount',
                    dispensed=amount, limit=auth.max_amount))
        if problems:
            if auth.state in {'resolved', 'discrepancy', 'completed'}:
                # Second result for a single use authorization
                problems.append(gettext(
                        'fuel_station.msg_authorization_log_reuse'))
                cls._log([auth], 'discrepancy', '; '.join(problems))
                return False
            cls._set(auth, 'discrepancy', **values)
            cls._log([cls(auth.id)], 'discrepancy', '; '.join(problems))
            return False
        cls._set(auth, 'completed', **values)
        auth = cls(auth.id)
        auth._book()
        cls._log([auth], 'completed', transaction_ref or '')
        return True

    # Booking

    def _book(self):
        "Stock move at the real quantity, then hook for sales/invoices"
        pool = Pool()
        Move = pool.get('stock.move')
        Shift = pool.get('fuel.shift')
        Location = pool.get('stock.location')
        shifts = Shift.search([
                ('station', '=', self.station.id), ('state', '=', 'open')],
            limit=1)
        values = {'shift': shifts[0].id if shifts else None}
        locations = Location.search([('type', '=', 'customer')], limit=1)
        if locations and self.dispensed_quantity > 0:
            move = Move()
            move.company = self.company
            move.currency = self.company.currency
            move.product = self.nozzle.tank.product
            move.unit = move.product.default_uom
            move.quantity = self.dispensed_quantity
            move.from_location = self.nozzle.tank.location
            move.to_location = locations[0]
            move.effective_date = pool.get('ir.date').today()
            move.unit_price = self.unit_price
            move.origin = self
            with without_check_access():
                move.save()
                Move.do([move])
            values['move'] = move.id
        with without_check_access():
            super(Authorization, self.__class__).write([self], values)
        self.__class__._after_complete(self.__class__(self.id))

    @classmethod
    def _after_complete(cls, auth):
        "Hook: fuel_station_sale creates the sale and invoice here"


class AuthorizationLog(ModelSQL, ModelView):
    "Fuel Dispensing Authorization Log"
    __name__ = 'fuel.authorization.log'

    authorization = fields.Many2One(
        'fuel.authorization', "Authorization", required=True,
        ondelete='CASCADE', readonly=True,
        help="Distribution authorization linked to this record.")
    timestamp = fields.DateTime("Date and Time", required=True, readonly=True,
        help="Date and time of the event.")
    user = fields.Many2One('res.user', "User", readonly=True,
        help="User concerned.")
    event = fields.Selection(EVENTS, "Event", required=True, readonly=True,
        help="Type of the event recorded in the audit log.")
    state = fields.Char("State", readonly=True,
        help="Current step of the workflow.")
    note = fields.Text("Note", readonly=True,
        help="Free remark visible on the order.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('id', 'ASC'))

    @classmethod
    def write(cls, *args):
        raise AuthorizationError(gettext(
                'fuel_station.msg_authorization_log_readonly'))

    @classmethod
    def delete(cls, logs):
        raise AuthorizationError(gettext(
                'fuel_station.msg_authorization_log_readonly'))


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.extend([
                ('fuel.authorization|expire',
                    "Expire Fuel Dispensing Authorizations"),
                ])
