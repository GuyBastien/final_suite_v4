# This file is part of the Tryton hotel_pms module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.modules.product import price_digits, round_price
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import ReservationError

_ZERO = Decimal(0)

_STATES = {'readonly': Eval('state') != 'draft'}
_CHARGE_STATES = {'readonly': Eval('state').in_(['checked_out', 'cancelled'])}


class Reservation(Workflow, ModelSQL, ModelView):
    "Hotel Reservation"
    __name__ = 'hotel.reservation'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    company = fields.Many2One(
        'company.company', "Company", required=True, states=_STATES)
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency"),
        'on_change_with_currency')
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'on_change_with_currency_digits')
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        domain=[('company', '=', Eval('company', -1))], states=_STATES)
    party = fields.Many2One(
        'party.party', "Guest", required=True,
        context={'company': Eval('company', -1)}, states=_STATES)
    adults = fields.Integer(
        "Adults", required=True, domain=[('adults', '>=', 1)],
        states=_STATES)
    children = fields.Integer(
        "Children", required=True, domain=[('children', '>=', 0)],
        states=_STATES)
    start = fields.DateTime("Arrival", required=True, states=_STATES)
    end = fields.DateTime(
        "Departure", required=True,
        domain=[If(Bool(Eval('start')), ('end', '>', Eval('start')), ())],
        states=_STATES)
    actual_start = fields.DateTime("Checked-in At", readonly=True)
    actual_end = fields.DateTime("Checked-out At", readonly=True)
    lines = fields.One2Many(
        'hotel.reservation.line', 'reservation', "Rooms", states=_STATES)
    charges = fields.One2Many(
        'hotel.reservation.charge', 'reservation', "Charges",
        states=_CHARGE_STATES)
    total_amount = fields.Function(
        fields.Numeric(
            "Total", digits=(16, Eval('currency_digits', 2))),
        'get_total_amount')
    sale = fields.Many2One('sale.sale', "Sale", readonly=True)
    invoice_recurrence = fields.Many2One(
        'sale.subscription.recurrence.rule.set', "Invoice Recurrence",
        states={'readonly': Bool(Eval('subscription'))},
        help="Long stays: rooms are billed by subscription with this "
        "recurrence instead of a single sale.")
    subscription = fields.Many2One(
        'sale.subscription', "Subscription", readonly=True)
    pos_sales = fields.One2Many(
        'sale.point.sale', 'reservation', "POS Sales", readonly=True)
    pos_amount = fields.Function(
        fields.Numeric("POS Charges", digits=(16, Eval('currency_digits', 2)),
            help="Point of sale charges not yet settled."),
        'get_pos_amount')
    sale_state = fields.Function(
        fields.Selection('get_sale_states', "Sale State"),
        'get_sale_state')
    invoice_state = fields.Function(
        fields.Selection('get_invoice_states', "Invoice State"),
        'get_sale_state')
    notes = fields.Text("Notes")
    state = fields.Selection([
            ('draft', "Draft"),
            ('confirmed', "Confirmed"),
            ('checked_in', "Checked In"),
            ('checked_out', "Checked Out"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'checked_in', 'success',
                        If(Eval('state') == 'checked_out', 'muted',
                        If(Eval('state') == 'cancelled', 'muted',
                        ''))))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('start', 'DESC'))
        cls._transitions |= {
            ('draft', 'confirmed'),
            ('confirmed', 'draft'),
            ('confirmed', 'checked_in'),
            ('checked_in', 'checked_out'),
            ('draft', 'cancelled'),
            ('confirmed', 'cancelled'),
            ('cancelled', 'draft'),
            }
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'confirmed']),
                    'depends': ['state'],
                    },
                'draft': {
                    'invisible': ~Eval('state').in_(['confirmed', 'cancelled']),
                    'depends': ['state'],
                    },
                'compute': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'confirm': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'check_in': {
                    'invisible': Eval('state') != 'confirmed',
                    'depends': ['state'],
                    },
                'check_out': {
                    'invisible': Eval('state') != 'checked_in',
                    'depends': ['state'],
                    },
                'create_subscription': {
                    'invisible': (
                        ~Eval('state').in_(['confirmed', 'checked_in'])
                        | Bool(Eval('sale')) | Bool(Eval('subscription'))),
                    'depends': ['state', 'sale', 'subscription'],
                    },
                'create_sale': {
                    'invisible': (
                        ~Eval('state').in_(['checked_in', 'checked_out'])
                        | Bool(Eval('sale'))),
                    'depends': ['state', 'sale'],
                    },
                })

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_adults(cls):
        return 1

    @classmethod
    def default_children(cls):
        return 0

    @classmethod
    def default_state(cls):
        return 'draft'

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    @fields.depends('company')
    def on_change_with_currency_digits(self, name=None):
        if self.company:
            return self.company.currency.digits
        return 2

    @classmethod
    def get_sale_states(cls):
        Sale = Pool().get('sale.sale')
        return [(None, "")] + Sale.fields_get(['state'])['state']['selection']

    @classmethod
    def get_invoice_states(cls):
        Sale = Pool().get('sale.sale')
        return [(None, "")] + Sale.fields_get(
            ['invoice_state'])['invoice_state']['selection']

    def get_sale_state(self, name):
        if self.sale:
            return getattr(self.sale, name.replace('sale_', ''))

    def get_total_amount(self, name):
        total = sum((l.amount or Decimal(0) for l in self.lines), Decimal(0))
        total += sum((c.amount or Decimal(0) for c in self.charges), Decimal(0))
        return self.company.currency.round(total)

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(
            ModelData.get_id('hotel_pms', 'sequence_reservation'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        for name in ['number', 'actual_start', 'actual_end', 'sale',
                'subscription']:
            default.setdefault(name, None)
        default.setdefault('charges', None)
        default.setdefault('pos_sales', None)
        return super().copy(reservations, default=default)

    @classmethod
    def delete(cls, reservations):
        for reservation in reservations:
            if reservation.state not in {'draft', 'cancelled'}:
                raise ReservationError(gettext(
                        'hotel_pms.msg_reservation_delete_state',
                        reservation=reservation.rec_name))
        super().delete(reservations)

    # Business logic

    def compute_lines(self, only_missing=False):
        "Compute the price of the room lines from the hotel rates"
        Line = Pool().get('hotel.reservation.line')
        lines = []
        for line in self.lines:
            if only_missing and line.unit_price is not None:
                continue
            line.compute(self.start, self.end)
            lines.append(line)
        Line.save(lines)

    def check_confirmable(self):
        if not self.lines:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_no_lines',
                    reservation=self.rec_name))
        rooms = set()
        for line in self.lines:
            room = line.room
            if room is None:
                raise ReservationError(gettext(
                        'hotel_pms.msg_reservation_line_no_room',
                        reservation=self.rec_name,
                        room_type=line.room_type.rec_name))
            if room in rooms:
                raise ReservationError(gettext(
                        'hotel_pms.msg_reservation_room_duplicate',
                        reservation=self.rec_name, room=room.rec_name))
            rooms.add(room)
            if not room.is_available(self.start, self.end, exclude=self):
                raise ReservationError(gettext(
                        'hotel_pms.msg_reservation_room_unavailable',
                        reservation=self.rec_name, room=room.rec_name,
                        start=self.start, end=self.end))

    def import_consumptions(self):
        """Add as charges the billable consumptions linked to the stay
        (each consumption line is billed only once)."""
        pool = Pool()
        Charge = pool.get('hotel.reservation.charge')
        ConsumptionLine = pool.get('hotel.consumption.line')
        candidates = ConsumptionLine.search([
                ('consumption.state', '=', 'done'),
                ('consumption.kind', '=', 'consumption'),
                ('consumption.reservation', '=', self.id),
                ('billable', '=', True),
                ])
        used = {c.consumption_line.id for c in Charge.search([
                    ('consumption_line', 'in', [c.id for c in candidates])])}
        charges = []
        for line in candidates:
            if line.id in used:
                continue
            charge = Charge()
            charge.reservation = self
            charge.product = line.product
            charge.description = line.product.rec_name
            charge.quantity = line.quantity
            charge.unit = line.unit
            charge.unit_price = line.unit_price
            charge.consumption_line = line
            charges.append(charge)
        Charge.save(charges)

    def _sale_charges(self):
        "Charges invoiced to the guest (extension hook)"
        return list(self.charges)

    def process_sale(self):
        "Create the draft sale invoiced to the guest"
        pool = Pool()
        Sale = pool.get('sale.sale')
        Date = pool.get('ir.date')
        if self.sale:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_sale_exists',
                    reservation=self.rec_name))
        # With a subscription the rooms are already billed: only the
        # charges are sold
        if self.subscription and not (
                self.charges or self.get_pos_payments()):
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_nothing_to_sell',
                    reservation=self.rec_name))
        sale = Sale()
        sale.company = self.company
        sale.currency = self.company.currency
        sale.party = self.party
        sale.sale_date = Date.today()
        sale.description = self.rec_name
        sale.origin = self
        sale.on_change_party()
        sale_lines = []
        if not self.subscription:
            for line in self.lines:
                sale_lines.append(line.get_sale_line(sale))
        for charge in self._sale_charges():
            sale_lines.append(charge.get_sale_line(sale))
        pos_lines, pos_payments = self._pos_sale_lines(sale)
        sale_lines.extend(pos_lines)
        sale.lines = sale_lines
        sale.save()
        if pos_payments:
            pool.get('sale.point.payment').write(
                pos_payments, {'settled': True})
        self.sale = sale
        self.save()
        return sale

    def get_pos_payments(self):
        "Room charge payments of finished POS sales not yet settled"
        Payment = Pool().get('sale.point.payment')
        return Payment.search([
                ('sale.reservation', '=', self.id),
                ('sale.state', 'in', ['done', 'posted']),
                ('method.room_charge', '=', True),
                ('settled', '=', False),
                ])

    def get_pos_amount(self, name):
        total = sum((p.amount for p in self.get_pos_payments()), _ZERO)
        return self.company.currency.round(total)

    def _pos_sale_lines(self, sale):
        "Return the sale line settling the room charges (and the payments)"
        payments = self.get_pos_payments()
        if not payments:
            return [], []
        product = self.hotel.room_charge_product
        if product is None:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_no_room_charge_product',
                    hotel=self.hotel.rec_name))
        SaleLine = Pool().get('sale.line')
        line = SaleLine()
        line.sale = sale
        line.product = product
        line.on_change_product()
        line.quantity = 1.
        line.unit_price = sum((p.amount for p in payments), _ZERO)
        line.description = gettext(
            'hotel_pms.msg_pos_room_charge_line', reservation=self.rec_name)
        return [line], payments

    def process_subscription(self):
        "Bill the rooms of a long stay with a subscription"
        pool = Pool()
        Subscription = pool.get('sale.subscription')
        Line = pool.get('sale.subscription.line')
        Service = pool.get('sale.subscription.service')
        if self.sale or self.subscription:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_billed',
                    reservation=self.rec_name))
        if not self.invoice_recurrence:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_no_recurrence',
                    reservation=self.rec_name))
        start = self.start.date()
        # The departure day is not a night of the stay
        end = max(self.end.date() - datetime.timedelta(days=1), start)
        subscription = Subscription()
        subscription.company = self.company
        subscription.party = self.party
        subscription.currency = self.company.currency
        subscription.start_date = start
        subscription.end_date = end
        subscription.invoice_start_date = start
        subscription.invoice_recurrence = self.invoice_recurrence
        subscription.description = self.rec_name
        subscription.on_change_party()
        subscription.invoice_recurrence = self.invoice_recurrence
        lines = []
        for rline in self.lines:
            product = rline.room_type.night_product
            services = Service.search([('product', '=', product.id)],
                limit=1) if product else []
            if not services:
                raise ReservationError(gettext(
                        'hotel_pms.msg_reservation_no_service',
                        room_type=rline.room_type.rec_name))
            line = Line()
            line.subscription = subscription
            line.service = services[0]
            line.on_change_service()
            line.quantity = 1.
            line.description = "%s - %s" % (
                rline.room.rec_name if rline.room else
                rline.room_type.rec_name, self.rec_name)
            # One night per day at the price agreed in the reservation
            night_price = rline.nightly_price()
            line.unit_price = night_price
            line.start_date = start
            line.end_date = end
            lines.append(line)
        subscription.lines = lines
        subscription.save()
        self.subscription = subscription
        self.save()
        return subscription

    # Workflow

    @classmethod
    @ModelView.button
    def compute(cls, reservations):
        for reservation in reservations:
            reservation.compute_lines()

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, reservations):
        for reservation in reservations:
            reservation.check_confirmable()
            reservation.compute_lines(only_missing=True)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, reservations):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, reservations):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_in')
    def check_in(cls, reservations):
        Room = Pool().get('hotel.room')
        rooms = []
        for reservation in reservations:
            for line in reservation.lines:
                if line.room.state != 'available':
                    raise ReservationError(gettext(
                            'hotel_pms.msg_reservation_room_not_ready',
                            reservation=reservation.rec_name,
                            room=line.room.rec_name,
                            state=line.room.state))
                rooms.append(line.room)
        Room.write(rooms, {'state': 'occupied'})
        cls.write(reservations, {'actual_start': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_out')
    def check_out(cls, reservations):
        Room = Pool().get('hotel.room')
        rooms = []
        for reservation in reservations:
            reservation.import_consumptions()
            rooms.extend(l.room for l in reservation.lines if l.room)
        Room.write(rooms, {'state': 'dirty'})
        cls.write(reservations, {'actual_end': datetime.datetime.now()})

    @classmethod
    @ModelView.button
    def create_subscription(cls, reservations):
        for reservation in reservations:
            reservation.process_subscription()

    @classmethod
    @ModelView.button
    def create_sale(cls, reservations):
        for reservation in reservations:
            reservation.process_sale()


class ReservationLine(ModelSQL, ModelView):
    "Hotel Reservation Line"
    __name__ = 'hotel.reservation.line'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", required=True,
        ondelete='CASCADE')
    hotel = fields.Function(
        fields.Many2One('hotel.hotel', "Hotel"), 'on_change_with_hotel')
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'on_change_with_currency_digits')
    room_type = fields.Many2One(
        'hotel.room.type', "Room Type", required=True,
        domain=[('hotel', '=', Eval('hotel', -1))])
    room = fields.Many2One(
        'hotel.room', "Room",
        domain=[
            ('hotel', '=', Eval('hotel', -1)),
            ('type', '=', Eval('room_type', -1)),
            ],
        help="Required to confirm the reservation.")
    pricing = fields.Selection([
            ('auto', "Automatic"),
            ('hourly', "Hourly"),
            ('nightly', "Nightly"),
            ], "Pricing", required=True,
        help="Automatic uses the hourly rate for short stays and the "
        "nightly rate otherwise.")
    billed_as = fields.Selection([
            (None, ""),
            ('hourly', "Hourly"),
            ('nightly', "Nightly"),
            ], "Billed As", readonly=True)
    quantity = fields.Numeric(
        "Quantity", digits=(16, 2),
        help="Number of hours or nights.")
    unit_price = fields.Numeric("Unit Price", digits=price_digits)
    discount_rate = fields.Numeric(
        "Discount Rate", digits=(16, 4),
        domain=[('discount_rate', '>=', 0), ('discount_rate', '<=', 1)],
        help="Discount applied on the room price (0.10 for 10%).")
    amount = fields.Function(
        fields.Numeric("Amount", digits=(16, Eval('currency_digits', 2))),
        'on_change_with_amount')

    @classmethod
    def default_discount_rate(cls):
        return Decimal(0)

    @classmethod
    def default_pricing(cls):
        return 'auto'

    @fields.depends('reservation', '_parent_reservation.hotel')
    def on_change_with_hotel(self, name=None):
        if self.reservation and self.reservation.hotel:
            return self.reservation.hotel.id

    @fields.depends('reservation', '_parent_reservation.company')
    def on_change_with_currency_digits(self, name=None):
        if self.reservation and self.reservation.company:
            return self.reservation.company.currency.digits
        return 2

    @fields.depends(
        'quantity', 'unit_price', 'discount_rate', 'reservation',
        '_parent_reservation.company')
    def on_change_with_amount(self, name=None):
        if self.quantity is None or self.unit_price is None:
            return Decimal(0)
        amount = self.quantity * self.unit_price
        amount *= 1 - (self.discount_rate or 0)
        if self.reservation and self.reservation.company:
            return self.reservation.company.currency.round(amount)
        return amount

    def compute(self, start, end):
        "Set the quantity and unit price from the hotel rates"
        mode = None if self.pricing == 'auto' else self.pricing
        price = self.room_type.compute_price(start, end, mode=mode)
        self.billed_as = price['mode']
        self.quantity = price['quantity']
        self.unit_price = round_price(
            price['amount'] / price['quantity'])

    def nightly_price(self):
        "Price of one night after discount"
        if self.billed_as == 'hourly' or not self.unit_price:
            # Short stay billed per hour: one night is the nightly rate
            price = self.room_type.compute_price(
                self.reservation.start, self.reservation.end,
                mode='nightly')
            amount = price['amount'] / price['quantity']
        else:
            amount = self.unit_price
        amount *= 1 - (self.discount_rate or 0)
        return round_price(amount)

    def get_sale_line(self, sale):
        pool = Pool()
        SaleLine = pool.get('sale.line')
        if self.billed_as == 'hourly':
            product = self.room_type.hour_product
        else:
            product = self.room_type.night_product
        if product is None:
            raise ReservationError(gettext(
                    'hotel_pms.msg_reservation_no_product',
                    room_type=self.room_type.rec_name,
                    mode=self.billed_as or 'nightly'))
        line = SaleLine()
        line.sale = sale
        line.product = product
        line.on_change_product()
        line.quantity = float(self.quantity)
        line.unit_price = self.unit_price
        if self.discount_rate:
            line.base_price = self.unit_price
            line.discount_rate = self.discount_rate
            line.on_change_discount_rate()
        line.description = "%s - %s" % (
            self.room.rec_name if self.room else self.room_type.rec_name,
            self.reservation.rec_name)
        return line


class ReservationCharge(ModelSQL, ModelView):
    "Hotel Reservation Charge"
    __name__ = 'hotel.reservation.charge'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", required=True,
        ondelete='CASCADE')
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'on_change_with_currency_digits')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[('salable', '=', True)],
        context={'company': Eval('company', -1)})
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    description = fields.Char("Description")
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', "Product UoM Category"),
        'on_change_with_product_uom_category')
    quantity = fields.Float(
        "Quantity", digits='unit', required=True,
        domain=[('quantity', '>', 0)])
    unit = fields.Many2One(
        'product.uom', "Unit", required=True,
        domain=[('category', '=', Eval('product_uom_category', -1))])
    unit_price = fields.Numeric("Unit Price", digits=price_digits,
        required=True)
    amount = fields.Function(
        fields.Numeric("Amount", digits=(16, Eval('currency_digits', 2))),
        'on_change_with_amount')
    consumption_line = fields.Many2One(
        'hotel.consumption.line', "Consumption", readonly=True)

    @classmethod
    def default_quantity(cls):
        return 1.

    @fields.depends('reservation', '_parent_reservation.company')
    def on_change_with_company(self, name=None):
        if self.reservation and self.reservation.company:
            return self.reservation.company.id

    @fields.depends('reservation', '_parent_reservation.company')
    def on_change_with_currency_digits(self, name=None):
        if self.reservation and self.reservation.company:
            return self.reservation.company.currency.digits
        return 2

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('product', 'unit', 'unit_price', 'description')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.sale_uom
            self.unit_price = self.product.list_price
            self.description = self.product.rec_name

    @fields.depends(
        'quantity', 'unit_price', 'reservation', '_parent_reservation.company')
    def on_change_with_amount(self, name=None):
        if self.quantity is None or self.unit_price is None:
            return Decimal(0)
        amount = Decimal(str(self.quantity)) * self.unit_price
        if self.reservation and self.reservation.company:
            return self.reservation.company.currency.round(amount)
        return amount

    @classmethod
    def search_company(cls, name, clause):
        return [('reservation.company',) + tuple(clause[1:])]

    @classmethod
    def copy(cls, charges, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('consumption_line', None)
        return super().copy(charges, default=default)

    def get_sale_line(self, sale):
        SaleLine = Pool().get('sale.line')
        line = SaleLine()
        line.sale = sale
        line.product = self.product
        line.on_change_product()
        line.quantity = self.quantity
        line.unit = self.unit
        line.unit_price = self.unit_price
        line.description = self.description or self.product.rec_name
        return line


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.sale'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['hotel.reservation']
