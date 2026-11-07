# This file is part of the Tryton hotel_billing module.
import datetime
from collections import OrderedDict
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.modules.product import price_digits, round_price
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import BillingError

_ZERO = Decimal(0)

PAYMENT_METHODS = [
    ('cash', "Cash"),
    ('card', "Card"),
    ('mobile', "Mobile Money"),
    ('transfer', "Bank Transfer"),
    ('cheque', "Cheque"),
    ('other', "Other"),
    ]


class Party(metaclass=PoolMeta):
    __name__ = 'party.party'

    hotel_corporate = fields.Boolean(
        "Corporate Account",
        help="Companies and agencies whose guests' stays are invoiced "
        "to them (city ledger).")
    hotel_credit_limit = fields.Numeric(
        "Credit Limit", digits=(16, 2),
        domain=[If(Bool(Eval('hotel_credit_limit')),
                ('hotel_credit_limit', '>=', 0), ())],
        states={'invisible': ~Eval('hotel_corporate', False)},
        help="Reservations cannot be billed to the account beyond this "
        "amount of receivable. Empty means no limit.")

    @classmethod
    def default_hotel_corporate(cls):
        return False


class TouristTax(DeactivableMixin, ModelSQL, ModelView):
    "Tourist Tax"
    __name__ = 'hotel.tourist.tax'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        context={'company': Eval('company', -1)},
        help="The service invoiced for the tax.")
    amount = fields.Numeric(
        "Amount", digits=price_digits, required=True,
        domain=[('amount', '>=', 0)],
        help="Amount per person and per night.")
    children_exempt = fields.Boolean("Children Exempt")
    max_nights = fields.Integer(
        "Maximum Nights", domain=[If(Bool(Eval('max_nights')),
                ('max_nights', '>=', 1), ())],
        help="The tax is not due beyond this number of nights. "
        "Empty means no limit.")

    @classmethod
    def default_children_exempt(cls):
        return True

    @fields.depends('hotel', '_parent_hotel.company')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    deposit_product = fields.Many2One(
        'product.product', "Deposit Product",
        domain=[('type', '=', 'service'), ('salable', '=', True)],
        context={'company': Eval('company', -1)},
        help="The service used to deduct the deposits from the invoice "
        "(use an account of liabilities).")
    tourist_taxes = fields.One2Many(
        'hotel.tourist.tax', 'hotel', "Tourist Taxes")


class Deposit(ModelSQL, ModelView):
    "Reservation Deposit"
    __name__ = 'hotel.deposit'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", required=True,
        ondelete='CASCADE',
        states={'readonly': Bool(Eval('id', -1) >= 0)})
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"),
        'on_change_with_currency_digits')
    date = fields.Date("Date", required=True)
    amount = fields.Numeric(
        "Amount", digits=(16, Eval('currency_digits', 2)), required=True,
        domain=[('amount', '>', 0)],
        states={'readonly': Eval('state') != 'received'})
    method = fields.Selection(PAYMENT_METHODS, "Method", required=True)
    reference = fields.Char("Reference")
    state = fields.Selection([
            ('received', "Received"),
            ('applied', "Applied"),
            ('refunded', "Refunded"),
            ], "State", readonly=True, required=True)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'received', 'success',
                        If(Eval('state') == 'applied', 'muted',
                        If(Eval('state') == 'refunded', 'muted',
                        '')))),
            ]
    notes = fields.Text("Notes")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._buttons.update({
                'refund': {
                    'invisible': Eval('state') != 'received',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_method(cls):
        return 'cash'

    @classmethod
    def default_state(cls):
        return 'received'

    @fields.depends('reservation', '_parent_reservation.company')
    def on_change_with_company(self, name=None):
        if self.reservation and self.reservation.company:
            return self.reservation.company.id

    @fields.depends('reservation', '_parent_reservation.company')
    def on_change_with_currency_digits(self, name=None):
        if self.reservation and self.reservation.company:
            return self.reservation.company.currency.digits
        return 2

    @classmethod
    def search_company(cls, name, clause):
        return [('reservation.company',) + tuple(clause[1:])]

    @classmethod
    def delete(cls, deposits):
        for deposit in deposits:
            if deposit.state == 'applied':
                raise BillingError(gettext(
                        'hotel_billing.msg_deposit_delete_applied',
                        deposit=deposit.rec_name))
        super().delete(deposits)

    @classmethod
    @ModelView.button
    def refund(cls, deposits):
        cls.write(deposits, {'state': 'refunded'})


class NightAudit(ModelSQL, ModelView):
    "Night Audit"
    __name__ = 'hotel.night.audit'

    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        states={'readonly': Eval('state') != 'draft'})
    date = fields.Date(
        "Date", required=True, states={'readonly': Eval('state') != 'draft'})
    user = fields.Many2One('res.user', "User", readonly=True)
    state = fields.Selection([
            ('draft', "Draft"),
            ('done', "Done"),
            ], "State", readonly=True, required=True)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'done', 'muted',
                        ''))),
            ]
    in_house = fields.Integer("In House", readonly=True)
    arrivals = fields.Integer("Arrivals", readonly=True)
    departures = fields.Integer("Departures", readonly=True)
    no_shows = fields.Integer("No-Shows", readonly=True)
    rooms_dirty = fields.Integer("Dirty Rooms", readonly=True)
    room_revenue = fields.Numeric(
        "Room Revenue", digits=(16, 2), readonly=True)
    notes = fields.Text("Notes")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('hotel_date_unique', Unique(t, t.hotel, t.date),
                'hotel_billing.msg_audit_unique'),
            ]
        cls._order.insert(0, ('date', 'DESC'))
        cls._buttons.update({
                'close_day': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @fields.depends('hotel', '_parent_hotel.company')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]

    @classmethod
    def delete(cls, audits):
        for audit in audits:
            if audit.state == 'done':
                raise BillingError(gettext(
                        'hotel_billing.msg_audit_delete_done',
                        audit=audit.rec_name))
        super().delete(audits)

    def get_rec_name(self, name):
        return "%s %s" % (self.hotel.code, self.date)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('hotel.code',) + tuple(clause[1:])]

    @staticmethod
    def _line_revenue(line, date):
        "Revenue of the room line for the night starting on date"
        reservation = line.reservation
        if line.billed_as == 'hourly':
            if reservation.start.date() == date:
                return (line.amount or _ZERO)
            return _ZERO
        return line.nightly_price()

    def compute(self):
        "Figures of the day and no-show handling"
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        Room = pool.get('hotel.room')
        day_start = datetime.datetime.combine(self.date, datetime.time.min)
        day_end = day_start + datetime.timedelta(days=1)
        base = [('hotel', '=', self.hotel.id)]
        in_house = Reservation.search(base + [
                ('state', '=', 'checked_in'),
                ('start', '<', day_end), ('end', '>', day_start)])
        arrivals = Reservation.search(base + [
                ('state', 'in', ['checked_in', 'checked_out']),
                ('actual_start', '>=', day_start),
                ('actual_start', '<', day_end)])
        departures = Reservation.search(base + [
                ('state', '=', 'checked_out'),
                ('actual_end', '>=', day_start),
                ('actual_end', '<', day_end)])
        no_shows = Reservation.search(base + [
                ('state', '=', 'confirmed'), ('start', '<', day_end)])
        revenue = _ZERO
        for reservation in in_house:
            for line in reservation.lines:
                revenue += self._line_revenue(line, self.date)
        names = []
        for reservation in no_shows:
            names.append(reservation.rec_name)
            Reservation.write([reservation], {'no_show': True})
        if no_shows:
            Reservation.cancel(no_shows)
        self.in_house = len(in_house)
        self.arrivals = len(arrivals)
        self.departures = len(departures)
        self.no_shows = len(no_shows)
        self.rooms_dirty = Room.search([
                ('hotel', '=', self.hotel.id), ('state', '=', 'dirty')],
            count=True)
        self.room_revenue = self.hotel.company.currency.round(revenue)
        if names:
            self.notes = ((self.notes + '\n') if self.notes else '') + \
                gettext('hotel_billing.msg_audit_no_shows',
                    reservations=', '.join(names))

    @classmethod
    @ModelView.button
    def close_day(cls, audits):
        for audit in audits:
            audit.compute()
            audit.user = Transaction().user
            audit.state = 'done'
        cls.save(audits)


class ReservationLine(metaclass=PoolMeta):
    __name__ = 'hotel.reservation.line'

    payer = fields.Many2One(
        'party.party', "Bill To",
        context={'company': Eval('_parent_reservation', {}).get(
                'company', -1)},
        help="Party invoiced for this room. Empty means the guest.")


class ReservationCharge(metaclass=PoolMeta):
    __name__ = 'hotel.reservation.charge'

    payer = fields.Many2One(
        'party.party', "Bill To",
        context={'company': Eval('_parent_reservation', {}).get(
                'company', -1)},
        help="Party invoiced for this charge. Empty means the guest.")
    tourist_tax = fields.Boolean("Tourist Tax", readonly=True)

    @classmethod
    def copy(cls, charges, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('tourist_tax', False)
        return super().copy(charges, default=default)


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    deposits = fields.One2Many('hotel.deposit', 'reservation', "Deposits")
    deposit_total = fields.Function(
        fields.Numeric(
            "Deposits", digits=(16, Eval('currency_digits', 2))),
        'get_deposit_total')
    balance_due = fields.Function(
        fields.Numeric(
            "Balance Due", digits=(16, Eval('currency_digits', 2))),
        'get_balance_due')
    tourist_tax_exempt = fields.Boolean(
        "Tourist Tax Exempt",
        states={'readonly': Eval('state').in_(['checked_out', 'cancelled'])})
    no_show = fields.Boolean("No-Show", readonly=True)
    sales = fields.Function(
        fields.One2Many('sale.sale', None, "Sales"), 'get_sales')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons['create_split_sales'] = {
            'invisible': (
                ~Eval('state').in_(['checked_in', 'checked_out'])
                | Bool(Eval('sale'))),
            'depends': ['state', 'sale'],
            }

    @classmethod
    def default_tourist_tax_exempt(cls):
        return False

    @classmethod
    def default_no_show(cls):
        return False

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('deposits', None)
        default.setdefault('no_show', False)
        return super().copy(reservations, default=default)

    def get_deposit_total(self, name):
        total = sum((d.amount for d in self.deposits
                if d.state == 'received'), _ZERO)
        return self.company.currency.round(total)

    def get_balance_due(self, name):
        total = (self.total_amount or _ZERO) + (self.pos_amount or _ZERO)
        return self.company.currency.round(
            total - self.get_deposit_total(None))

    def get_sales(self, name):
        Sale = Pool().get('sale.sale')
        return [s.id for s in Sale.search([
                    ('origin', '=', '%s,%s' % (self.__name__, self.id))])]

    # Tourist tax

    def import_consumptions(self):
        super().import_consumptions()
        self._add_tourist_taxes()

    def _add_tourist_taxes(self):
        Charge = Pool().get('hotel.reservation.charge')
        if self.tourist_tax_exempt or any(
                c.tourist_tax for c in self.charges):
            return
        nights = max((self.end.date() - self.start.date()).days, 1)
        charges = []
        for tax in self.hotel.tourist_taxes:
            if not tax.active:
                continue
            persons = self.adults + (
                0 if tax.children_exempt else self.children)
            billed = min(nights, tax.max_nights) if tax.max_nights \
                else nights
            quantity = persons * billed
            if not quantity or not tax.amount:
                continue
            charge = Charge()
            charge.reservation = self
            charge.product = tax.product
            charge.description = tax.name
            charge.quantity = float(quantity)
            charge.unit = tax.product.sale_uom
            charge.unit_price = tax.amount
            charge.tourist_tax = True
            charges.append(charge)
        Charge.save(charges)

    # Deposits

    def _deposit_lines(self, sale):
        "Negative sale line deducting the deposits received"
        deposits = [d for d in self.deposits if d.state == 'received']
        if not deposits:
            return []
        product = self.hotel.deposit_product
        if product is None:
            raise BillingError(gettext(
                    'hotel_billing.msg_deposit_no_product',
                    hotel=self.hotel.rec_name))
        Deposit = Pool().get('hotel.deposit')
        SaleLine = Pool().get('sale.line')
        line = SaleLine()
        line.sale = sale
        line.product = product
        line.on_change_product()
        line.quantity = 1.
        line.unit_price = -round_price(
            sum((d.amount for d in deposits), _ZERO))
        line.description = gettext(
            'hotel_billing.msg_deposit_line', reservation=self.rec_name)
        Deposit.write(deposits, {'state': 'applied'})
        return [line]

    def _pos_sale_lines(self, sale):
        lines, payments = super()._pos_sale_lines(sale)
        return lines + self._deposit_lines(sale), payments

    # Billing to several payers

    def _payer_sale(self, payer, lines, charges, guest_sale):
        pool = Pool()
        Sale = pool.get('sale.sale')
        Date = pool.get('ir.date')
        sale = Sale()
        sale.company = self.company
        sale.currency = self.company.currency
        sale.party = payer
        sale.sale_date = Date.today()
        sale.description = self.rec_name
        sale.origin = self
        sale.on_change_party()
        sale_lines = [l.get_sale_line(sale) for l in lines]
        sale_lines.extend(c.get_sale_line(sale) for c in charges)
        if guest_sale:
            pos_lines, pos_payments = self._pos_sale_lines(sale)
            sale_lines.extend(pos_lines)
        else:
            pos_payments = []
        sale.lines = sale_lines
        return sale, pos_payments

    def _check_credit(self, payer, sale):
        limit = payer.hotel_credit_limit
        if not payer.hotel_corporate or limit is None:
            return
        total = sum(
            ((Decimal(str(l.quantity or 0)) * (l.unit_price or _ZERO))
                for l in sale.lines if l.type == 'line'), _ZERO)
        receivable = payer.receivable or _ZERO
        if receivable + total > limit:
            raise BillingError(gettext(
                    'hotel_billing.msg_credit_limit_exceeded',
                    party=payer.rec_name, limit=limit,
                    amount=receivable + total))

    def process_split_sales(self):
        "Create one draft sale per payer (the guest by default)"
        pool = Pool()
        if self.sale or self.subscription:
            raise BillingError(gettext(
                    'hotel_billing.msg_split_billed',
                    reservation=self.rec_name))
        groups = OrderedDict()
        groups[self.party.id] = [self.party, [], []]
        for line in self.lines:
            payer = line.payer or self.party
            groups.setdefault(payer.id, [payer, [], []])[1].append(line)
        for charge in self._sale_charges():
            payer = charge.payer or self.party
            groups.setdefault(payer.id, [payer, [], []])[2].append(charge)
        sales = []
        payments = []
        for payer, lines, charges in groups.values():
            guest = payer.id == self.party.id
            if not (lines or charges) and not (
                    guest and (self.get_pos_payments() or any(
                            d.state == 'received' for d in self.deposits))):
                continue
            sale, pos_payments = self._payer_sale(
                payer, lines, charges, guest)
            self._check_credit(payer, sale)
            sales.append(sale)
            payments.extend(pos_payments)
        if not sales:
            raise BillingError(gettext(
                    'hotel_billing.msg_split_nothing',
                    reservation=self.rec_name))
        pool.get('sale.sale').save(sales)
        if payments:
            pool.get('sale.point.payment').write(payments, {'settled': True})
        self.sale = sales[0]
        self.save()
        return sales

    @classmethod
    @ModelView.button
    def create_split_sales(cls, reservations):
        for reservation in reservations:
            reservation.process_split_sales()
