# This file is part of the Tryton fuel_station module.
import datetime
from collections import defaultdict
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    Check, ModelSQL, ModelView, Workflow, Unique, fields)
from trytond.modules.product import price_digits
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction, without_check_access

from .exceptions import FuelWarning, ShiftError

PAYMENT_METHODS = [
    ('cash', "Cash"),
    ('card', "Card"),
    ('mobile', "Mobile Money"),
    ('voucher', "Fuel Voucher"),
    ('credit', "Customer Credit"),
    ]
_ZERO = Decimal(0)


def _authorized(shift, nozzle=None):
    "(quantity, amount) of the completed authorizations of the shift"
    Authorization = Pool().get('fuel.authorization')
    domain = [('shift', '=', shift.id), ('state', '=', 'completed')]
    if nozzle:
        domain.append(('nozzle', '=', nozzle.id))
    auths = Authorization.search(domain)
    return (sum(a.dispensed_quantity or 0. for a in auths),
        sum((a.dispensed_amount or _ZERO for a in auths), _ZERO))


def _decimal(value):
    return Decimal(str(round(value or 0., 3)))


class Shift(Workflow, ModelSQL, ModelView):
    "Fuel Shift"
    __name__ = 'fuel.shift'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="Company that owns the record.")
    station = fields.Many2One(
        'fuel.station', "Station", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': (Eval('state') != 'draft')
            | Bool(Eval('lines', []))},
        help="Station concerned.")
    attendant = fields.Many2One(
        'company.employee', "Attendant", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Employee in charge of the pumps during the shift.")
    date = fields.Date(
        "Date", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="Date of the document.")
    start_time = fields.DateTime("Start", readonly=True,
        help="Beginning of the period.")
    end_time = fields.DateTime("End", readonly=True,
        help="End of the period.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    lines = fields.One2Many(
        'fuel.shift.line', 'shift', "Pump Readings",
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Detail lines of the document.")
    payments = fields.One2Many(
        'fuel.shift.payment', 'shift', "Payments",
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Payments received for this record.")
    shop_sales = fields.One2Many(
        'fuel.shift.sale', 'shift', "Shop Sales",
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Shop items sold during the shift.")
    opening_float = fields.Numeric(
        "Opening Float", digits='currency',
        domain=[('opening_float', '>=', 0)],
        states={'readonly': Eval('state').in_(['closed', 'cancelled'])},
        help="Cash already in the drawer at the start of the shift. "
        "It is deducted from the counted cash.")
    total_quantity = fields.Function(
        fields.Float("Gallons Sold", digits=(16, 3),
        help="Total volume."), 'get_totals')
    fuel_sales = fields.Function(
        fields.Numeric("Fuel Sales", digits='currency',
        help="Sales of fuel during the shift."), 'get_totals')
    shop_total = fields.Function(
        fields.Numeric("Shop Sales", digits='currency',
        help="Total of the shop sales."), 'get_totals')
    total_sales = fields.Function(
        fields.Numeric("Sales", digits='currency',
        help="Total sales."), 'get_totals')
    total_paid = fields.Function(
        fields.Numeric("Collected", digits='currency',
        help="Total collected."), 'get_totals')
    difference = fields.Function(
        fields.Numeric("Difference", digits='currency',
        help="Difference between counted and expected amounts."), 'get_totals')
    authorized_total = fields.Function(
        fields.Numeric("Authorized Sales", digits='currency',
        help="Sales made under a validated authorization."),
        'get_totals')
    cash_expected = fields.Function(
        fields.Numeric("Cash Expected", digits='currency',
        help="Cash the shift should contain according to the sales."), 'get_totals')
    cash_counted = fields.Function(
        fields.Numeric("Cash Counted", digits='currency',
        help="Cash counted at the end of the shift."), 'get_totals')
    cash_difference = fields.Function(
        fields.Numeric("Cash Difference", digits='currency',
        help="Counted cash minus expected cash. A negative value is a shortage."), 'get_totals')
    suspect_total = fields.Function(
        fields.Numeric("Suspect Cash", digits='currency',
        help="Total value of doubtful banknotes and coins, excluded from the cash."), 'get_totals')
    notes = fields.Text("Notes",
        help="Free text for internal use.")
    state = fields.Selection([
            ('draft', "Draft"),
            ('open', "Open"),
            ('closed', "Closed"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True,
        help="Current step of the workflow.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {
            ('draft', 'open'),
            ('open', 'closed'),
            ('draft', 'cancelled'),
            ('open', 'cancelled'),
            ('cancelled', 'draft'),
            }
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'open']),
                    'depends': ['state'],
                    },
                'draft': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                'open_shift': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'close': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                'fetch_readings': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state') == 'cancelled', 'muted',
                    If(Eval('state') == 'open', 'warning',
                        If(Eval('state') == 'closed', 'success', '')))),
            ]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def default_opening_float(cls):
        return _ZERO

    @classmethod
    def default_station(cls):
        Station = Pool().get('fuel.station')
        stations = Station.search([
                ('company', '=', cls.default_company())], limit=2)
        if len(stations) == 1:
            return stations[0].id

    @classmethod
    def default_attendant(cls):
        User = Pool().get('res.user')
        user = User(Transaction().user)
        if user.employee:
            return user.employee.id

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        vlist = [v.copy() for v in vlist]
        sequence = Sequence(
            ModelData.get_id('fuel_station', 'sequence_shift'))
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, shifts, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        default.setdefault('start_time', None)
        default.setdefault('end_time', None)
        default.setdefault('payments', None)
        default.setdefault('lines', None)
        default.setdefault('shop_sales', None)
        return super().copy(shifts, default=default)

    @classmethod
    def delete(cls, shifts):
        for shift in shifts:
            if shift.state not in {'draft', 'cancelled'}:
                raise ShiftError(gettext(
                        'fuel_station.msg_shift_delete',
                        shift=shift.rec_name))
        super().delete(shifts)

    def _cash_count_values(self):
        """Return (counted, suspect, present) of the cash count of the shift.

        The core keeps no count of banknotes and coins: the module
        fuel_station_cash_control overrides this hook."""
        return _ZERO, _ZERO, False

    def _check_cash_count(self):
        "Hook called when the shift closes (see fuel_station_cash_control)."

    @classmethod
    def get_totals(cls, shifts, names):
        result = {name: {} for name in names}
        for shift in shifts:
            fuel = sum((l.amount or _ZERO for l in shift.lines), _ZERO)
            shop = sum((s.amount or _ZERO for s in shift.shop_sales), _ZERO)
            sales = fuel + shop
            paid = sum((p.amount or _ZERO for p in shift.payments), _ZERO)
            authorized = _authorized(shift)[1]
            cash = sum((p.amount or _ZERO for p in shift.payments
                    if p.method == 'cash'), _ZERO)
            counted, suspect, present = shift._cash_count_values()
            net = counted - (shift.opening_float or _ZERO)
            values = {
                'total_quantity': sum(l.quantity or 0. for l in shift.lines),
                'fuel_sales': fuel,
                'shop_total': shop,
                'total_sales': sales,
                'total_paid': paid,
                'authorized_total': authorized,
                'difference': paid + authorized - sales,
                'cash_expected': cash,
                'cash_counted': counted,
                'cash_difference': (net - cash) if present else _ZERO,
                'suspect_total': suspect,
                }
            for name in names:
                result[name][shift.id] = values[name]
        return result

    # Workflow

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, shifts):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, shifts):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('open')
    def open_shift(cls, shifts):
        pool = Pool()
        Line = pool.get('fuel.shift.line')
        Nozzle = pool.get('fuel.nozzle')
        Price = pool.get('fuel.price')
        new_lines = []
        for shift in shifts:
            others = cls.search([
                    ('station', '=', shift.station.id),
                    ('state', '=', 'open'),
                    ('id', '!=', shift.id)], limit=1)
            if others:
                raise ShiftError(gettext(
                        'fuel_station.msg_shift_already_open',
                        station=shift.station.rec_name,
                        shift=others[0].rec_name))
            if not shift.lines:
                nozzles = Nozzle.search([
                        ('pump.station', '=', shift.station.id),
                        ('pump.active', '=', True),
                        ('active', '=', True),
                        ('tank.active', '=', True)],
                    order=[('pump', 'ASC'), ('number', 'ASC')])
                if not nozzles:
                    raise ShiftError(gettext(
                            'fuel_station.msg_shift_no_nozzle',
                            station=shift.station.rec_name))
                for nozzle in nozzles:
                    new_lines.append({
                            'shift': shift.id,
                            'nozzle': nozzle.id,
                            'opening_index': nozzle.meter_index,
                            'test_quantity': 0.,
                            'unit_price': Price.get_price(
                                shift.station, nozzle.tank.product,
                                shift.date),
                            })
        if new_lines:
            Line.create(new_lines)
        cls.write(shifts, {'start_time': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    @Workflow.transition('closed')
    def close(cls, shifts):
        pool = Pool()
        Line = pool.get('fuel.shift.line')
        Move = pool.get('stock.move')
        Nozzle = pool.get('fuel.nozzle')
        Tank = pool.get('fuel.tank')
        Warning = pool.get('res.user.warning')
        for shift in shifts:
            if not shift.lines:
                raise ShiftError(gettext(
                        'fuel_station.msg_shift_no_lines',
                        shift=shift.rec_name))
            for line in shift.lines:
                if line.closing_index is None:
                    raise ShiftError(gettext(
                            'fuel_station.msg_shift_missing_index',
                            nozzle=line.nozzle.rec_name,
                            shift=shift.rec_name))
                if (line.test_quantity or 0.) > (
                        line.closing_index - line.opening_index + 1e-6):
                    raise ShiftError(gettext(
                            'fuel_station.msg_line_test_too_big',
                            nozzle=line.nozzle.rec_name))
        Line.refresh(
            [l for s in shifts for l in s.lines])
        shifts = cls.browse([s.id for s in shifts])
        for shift in shifts:
            # Book stock check: tanks cannot sell more than they hold
            sold = defaultdict(float)
            for line in shift.lines:
                sold[line.nozzle.tank] += line.stock_quantity()
            quantities = Tank.quantities(list(sold))
            for tank, gallons in sold.items():
                if gallons > quantities.get(tank.id, 0.) + 1e-6:
                    key = 'fuel_shift_stock_%s_%s' % (shift.id, tank.id)
                    if Warning.check(key):
                        raise FuelWarning(key, gettext(
                                'fuel_station.msg_shift_stock_short',
                                tank=tank.rec_name,
                                sold=gallons,
                                book=quantities.get(tank.id, 0.)))
            for payment in shift.payments:
                if payment.method == 'credit' and not payment.party:
                    raise ShiftError(gettext(
                            'fuel_station.msg_payment_credit_party',
                            shift=shift.rec_name))
            shift._check_cash_count()
            if abs(shift.difference) > shift.station.cash_tolerance:
                key = 'fuel_shift_cash_%s' % shift.id
                if Warning.check(key):
                    raise FuelWarning(key, gettext(
                            'fuel_station.msg_shift_cash_difference',
                            shift=shift.rec_name,
                            difference=shift.currency.round(
                                shift.difference)))
        moves, lines, nozzles = [], [], []
        for shift in shifts:
            customer = shift._customer_location()
            for line in shift.lines:
                if line.stock_quantity() <= 0:
                    continue
                line.move = line.get_move(customer)
                moves.append(line.move)
                lines.append(line)
            for line in shift.lines:
                nozzles.append(line.nozzle)
            for sale in shift.shop_sales:
                if not sale.move:
                    sale.move = sale.get_move(customer)
                    moves.append(sale.move)
                    lines.append(sale)
        with without_check_access():
            Move.save(moves)
            for record in lines:
                record.save()
            Move.do(moves)
            for shift in shifts:
                for line in shift.lines:
                    Nozzle.write([line.nozzle], {
                            'meter_index': line.closing_index})
        cls.write(shifts, {'end_time': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    def fetch_readings(cls, shifts):
        "Fill the closing indexes with the latest recorded readings"
        pool = Pool()
        Reading = pool.get('fuel.meter.reading')
        Line = pool.get('fuel.shift.line')
        for shift in shifts:
            for line in shift.lines:
                readings = Reading.search([
                        ('nozzle', '=', line.nozzle.id),
                        ('timestamp', '>=', shift.start_time
                            or datetime.datetime.min)],
                    order=[('timestamp', 'DESC'), ('id', 'DESC')], limit=1)
                if not readings:
                    continue
                index = readings[0].index
                if index < line.opening_index:
                    raise ShiftError(gettext(
                            'fuel_station.msg_reading_lower',
                            nozzle=line.nozzle.rec_name,
                            index=index, opening=line.opening_index))
                Line.write([line], {'closing_index': index})

    def _customer_location(self):
        Location = Pool().get('stock.location')
        locations = Location.search([('type', '=', 'customer')], limit=1)
        if not locations:
            raise ShiftError(gettext(
                    'fuel_station.msg_shift_no_customer_location'))
        return locations[0]


class ShiftLine(ModelSQL, ModelView):
    "Fuel Shift Pump Reading"
    __name__ = 'fuel.shift.line'

    shift = fields.Many2One(
        'fuel.shift', "Shift", required=True, ondelete='CASCADE',
        help="Shift concerned.")
    nozzle = fields.Many2One(
        'fuel.nozzle', "Nozzle", required=True,
        states={'readonly': Bool(Eval('move'))},
        help="Nozzle of the pump used.")
    product = fields.Function(
        fields.Many2One('product.product', "Fuel",
        help="Product concerned."),
        'on_change_with_product')
    unit = fields.Function(
        fields.Many2One('product.uom', "Unit",
        help="Unit of measure."), 'on_change_with_unit')
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    opening_index = fields.Float(
        "Opening Index", digits=(16, 3), required=True,
        domain=[('opening_index', '>=', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Meter index at the beginning of the shift.")
    closing_index = fields.Float(
        "Closing Index", digits=(16, 3),
        states={'readonly': Bool(Eval('move'))},
        help="Reading of the totalizer at the end of the shift.")
    test_quantity = fields.Float(
        "Test Quantity", digits=(16, 3), required=True,
        domain=[('test_quantity', '>=', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Fuel pumped for calibration/tests and poured back into the "
        "tank: it is not sold.")
    quantity = fields.Float("Gallons Sold", digits=(16, 3), readonly=True,
        help="Quantity, in the unit of measure.")
    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits, required=True,
        domain=[('unit_price', '>=', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Price of one unit before discount.")
    amount = fields.Numeric(
        "Amount", digits='currency', readonly=True,
        help="Amount in the currency of the document.")
    move = fields.Many2One('stock.move', "Stock Move", readonly=True,
        help="Stock move generated by this record.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('nozzle_unique', Unique(t, t.shift, t.nozzle),
                'fuel_station.msg_line_nozzle_unique'),
            ('index_order', Check(t, t.closing_index >= t.opening_index),
                'fuel_station.msg_line_index_order'),
            ]
        cls._order.insert(0, ('shift', 'ASC'))
        cls._order.insert(1, ('nozzle', 'ASC'))

    @classmethod
    def default_test_quantity(cls):
        return 0.

    @fields.depends('nozzle')
    def on_change_with_product(self, name=None):
        if self.nozzle and self.nozzle.tank:
            return self.nozzle.tank.product.id

    @fields.depends('nozzle')
    def on_change_with_unit(self, name=None):
        if self.nozzle and self.nozzle.tank:
            return self.nozzle.tank.product.default_uom.id

    @fields.depends('shift', '_parent_shift.currency')
    def on_change_with_currency(self, name=None):
        if self.shift and self.shift.currency:
            return self.shift.currency.id

    @fields.depends(
        'nozzle', 'opening_index', 'unit_price', 'shift',
        '_parent_shift.station', '_parent_shift.date')
    def on_change_nozzle(self):
        Price = Pool().get('fuel.price')
        if self.nozzle:
            self.opening_index = self.nozzle.meter_index
            if self.shift and self.shift.station and self.nozzle.tank:
                try:
                    self.unit_price = Price.get_price(
                        self.shift.station, self.nozzle.tank.product,
                        self.shift.date)
                except Exception:
                    pass

    def stock_quantity(self):
        "Gallons still to move: those already moved by authorizations are out"
        authorized = _authorized(self.shift, self.nozzle)[0]
        return max(0., round((self.quantity or 0.) - authorized, 3))

    def _quantities(self):
        "Return (gallons, amount) of the reading"
        if self.closing_index is None:
            return 0., _ZERO
        gallons = max(
            0., round(self.closing_index - self.opening_index
                - (self.test_quantity or 0.), 3))
        currency = self.shift.currency
        amount = currency.round(_decimal(gallons) * (self.unit_price or _ZERO))
        return gallons, amount

    @fields.depends(
        'opening_index', 'closing_index', 'test_quantity', 'unit_price',
        'shift', '_parent_shift.currency')
    def on_change_closing_index(self):
        if self.shift and self.shift.currency:
            self.quantity, self.amount = self._quantities()

    on_change_test_quantity = on_change_closing_index
    on_change_unit_price = on_change_closing_index

    @classmethod
    def refresh(cls, lines):
        "Store the gallons and amounts computed from the indexes"
        to_write = []
        for line in lines:
            gallons, amount = line._quantities()
            if line.quantity != gallons or line.amount != amount:
                to_write.extend(([line], {
                            'quantity': gallons, 'amount': amount}))
        if to_write:
            super().write(*to_write)

    @classmethod
    def create(cls, vlist):
        lines = super().create(vlist)
        cls.refresh(lines)
        return lines

    @classmethod
    def write(cls, *args):
        super().write(*args)
        actions = iter(args)
        lines = []
        for records, values in zip(actions, actions):
            if {'closing_index', 'opening_index', 'test_quantity',
                    'unit_price'} & set(values):
                lines.extend(records)
        if lines:
            cls.refresh(cls.browse([l.id for l in lines]))

    @classmethod
    def validate(cls, lines):
        super().validate(lines)
        for line in lines:
            if line.nozzle.station != line.shift.station:
                raise ShiftError(gettext(
                        'fuel_station.msg_line_nozzle_station',
                        nozzle=line.nozzle.rec_name,
                        station=line.shift.station.rec_name))
            if (line.closing_index is not None
                    and line.closing_index < line.opening_index):
                raise ShiftError(gettext(
                        'fuel_station.msg_line_index_order'))

    @classmethod
    def delete(cls, lines):
        for line in lines:
            if line.move:
                raise ShiftError(gettext(
                        'fuel_station.msg_line_delete_move',
                        nozzle=line.nozzle.rec_name))
        super().delete(lines)

    @classmethod
    def copy(cls, lines, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('move', None)
        default.setdefault('closing_index', None)
        default.setdefault('quantity', 0.)
        default.setdefault('amount', _ZERO)
        return super().copy(lines, default=default)

    def get_move(self, to_location):
        pool = Pool()
        Move = pool.get('stock.move')
        shift = self.shift
        move = Move()
        move.company = shift.company
        move.currency = shift.company.currency
        move.product = self.nozzle.tank.product
        move.unit = self.nozzle.tank.product.default_uom
        move.quantity = self.stock_quantity()
        move.from_location = self.nozzle.tank.location
        move.to_location = to_location
        move.effective_date = shift.date
        move.unit_price = self.unit_price
        move.origin = self
        return move


class ShiftPayment(ModelSQL, ModelView):
    "Fuel Shift Payment"
    __name__ = 'fuel.shift.payment'

    shift = fields.Many2One(
        'fuel.shift', "Shift", required=True, ondelete='CASCADE',
        help="Shift concerned.")
    method = fields.Selection(PAYMENT_METHODS, "Method", required=True,
        help="How the customer pays.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    amount = fields.Numeric(
        "Amount", digits='currency', required=True,
        domain=[('amount', '>=', 0)],
        help="Amount in the currency of the document.")
    reference = fields.Char("Reference",
        help="Reference of the payment (receipt number, voucher...).")
    party = fields.Many2One(
        'party.party', "Customer",
        context={'company': Eval('shift_company', -1)},
        states={'required': Eval('method') == 'credit',
            'invisible': Eval('method') != 'credit'},
        help="Customer who owes the amount (credit sales).")
    shift_company = fields.Function(
        fields.Many2One('company.company', "Company",
        help="Company of the shift."),
        'on_change_with_shift_company', searcher='search_shift_company')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('shift', 'ASC'))
        cls._order.insert(1, ('id', 'ASC'))

    @fields.depends('shift', '_parent_shift.company')
    def on_change_with_shift_company(self, name=None):
        if self.shift and self.shift.company:
            return self.shift.company.id

    @classmethod
    def search_shift_company(cls, name, clause):
        return [('shift.company',) + tuple(clause[1:])]

    @fields.depends('method', 'party')
    def on_change_method(self):
        if self.method != 'credit':
            self.party = None

    @classmethod
    def default_method(cls):
        return 'cash'

    @fields.depends('shift', '_parent_shift.currency')
    def on_change_with_currency(self, name=None):
        if self.shift and self.shift.currency:
            return self.shift.currency.id

    @classmethod
    def create(cls, vlist):
        cls._check_open(vlist=vlist)
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for payments, _ in zip(actions, actions):
            cls._check_open(payments=payments)
        super().write(*args)

    @classmethod
    def delete(cls, payments):
        cls._check_open(payments=payments)
        super().delete(payments)

    @classmethod
    def _check_open(cls, payments=(), vlist=()):
        Shift = Pool().get('fuel.shift')
        shifts = [p.shift for p in payments]
        shifts += [Shift(v['shift']) for v in vlist if v.get('shift')]
        for shift in shifts:
            if shift.state in {'closed', 'cancelled'}:
                raise ShiftError(gettext(
                        'fuel_station.msg_payment_shift_closed',
                        shift=shift.rec_name))


class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + [
            'fuel.shift.line', 'fuel.shift.sale', 'fuel.delivery.line',
            'fuel.dip', 'fuel.authorization']
