# This file is part of the Tryton hotel_pms module.
import datetime
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

from trytond.model import ModelView, fields
from trytond.modules.hotel.article import HOTEL_CATEGORIES
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.report import Report
from trytond.transaction import Transaction
from trytond.wizard import Button, StateReport, StateView, Wizard


_BILLED = ['confirmed', 'checked_in', 'checked_out']
_ZERO = Decimal(0)


def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


def _stay_days(reservation):
    "Dates on which the stay counts as occupied (night convention)"
    first = reservation.start.date()
    last = reservation.end.date()
    if last <= first:
        return [first]
    return [first + datetime.timedelta(days=i)
        for i in range((last - first).days)]


def _lines(hotel, start, end):
    "Billed room lines whose stay touches [start, end]"
    Line = Pool().get('hotel.reservation.line')
    low = datetime.datetime.combine(start, datetime.time.min)
    high = datetime.datetime.combine(end, datetime.time.max)
    return Line.search([
            ('reservation.hotel', '=', hotel.id),
            ('reservation.state', 'in', _BILLED),
            ('reservation.start', '<=', high),
            ('reservation.end', '>=', low),
            ])


def occupancy_rows(hotel, start, end):
    """Daily occupancy, room revenue, ADR and RevPAR.

    The revenue of a line is spread evenly over the nights it covers.
    """
    Room = Pool().get('hotel.room')
    total = Room.search_count([('hotel', '=', hotel.id)])
    occupied = defaultdict(int)
    revenue = defaultdict(lambda: _ZERO)
    for line in _lines(hotel, start, end):
        days = _stay_days(line.reservation)
        share = (line.amount or _ZERO) / len(days)
        for day in days:
            if start <= day <= end:
                occupied[day] += 1
                revenue[day] += share
    rows = []
    for day in _days(start, end):
        rows.append(_occupancy_row(day, total, occupied[day], revenue[day]))
    return rows


def _occupancy_row(label, total, occupied, revenue):
    rate = (Decimal(occupied) * 100 / total) if total else _ZERO
    adr = (revenue / occupied) if occupied else _ZERO
    revpar = (revenue / total) if total else _ZERO
    return SimpleNamespace(
        date=label, rooms=total, occupied=occupied, rate=rate,
        revenue=revenue, adr=adr, revpar=revpar)


def occupancy_total(rows):
    "Aggregate of the daily rows (room-nights available vs sold)"
    available = sum(r.rooms for r in rows)
    sold = sum(r.occupied for r in rows)
    revenue = sum((r.revenue for r in rows), _ZERO)
    return _occupancy_row(None, available, sold, revenue)


def revenue_rows(hotel, start, end):
    """Revenue by room type and by hotel article category.

    Room revenue is spread over the nights of the stay; charges are
    recognized on the check-out date.
    """
    Charge = Pool().get('hotel.reservation.charge')
    categories = dict(HOTEL_CATEGORIES)
    groups = {}

    def add(key, label, quantity, amount):
        row = groups.setdefault(key, SimpleNamespace(
                label=label, quantity=0, amount=_ZERO))
        row.quantity += quantity
        row.amount += amount

    for line in _lines(hotel, start, end):
        days = _stay_days(line.reservation)
        share = (line.amount or _ZERO) / len(days)
        count = sum(1 for d in days if start <= d <= end)
        if count:
            add((0, line.room_type.rec_name),
                line.room_type.rec_name,
                (line.quantity or 0) * count / len(days), share * count)
    low = datetime.datetime.combine(start, datetime.time.min)
    high = datetime.datetime.combine(end, datetime.time.max)
    for charge in Charge.search([
                ('reservation.hotel', '=', hotel.id),
                ('reservation.state', 'in', _BILLED)]):
        reservation = charge.reservation
        when = reservation.actual_end or reservation.end
        if not (low <= when <= high):
            continue
        category = charge.product.template.hotel_category
        label = categories.get(category) or "Other"
        add((1, label), label, charge.quantity, charge.amount or _ZERO)
    return [groups[k] for k in sorted(groups)]


def movement_rows(hotel, start, end, kind):
    "Arrivals (kind='start') or departures (kind='end') of the period"
    Reservation = Pool().get('hotel.reservation')
    states = (_BILLED if kind == 'start' else ['checked_in', 'checked_out'])
    low = datetime.datetime.combine(start, datetime.time.min)
    high = datetime.datetime.combine(end, datetime.time.max)
    reservations = Reservation.search([
            ('hotel', '=', hotel.id),
            ('state', 'in', states),
            (kind, '>=', low), (kind, '<=', high),
            ], order=[(kind, 'ASC'), ('number', 'ASC')])
    state_names = dict(Reservation.fields_get(['state'])['state']['selection'])
    rows = []
    for r in reservations:
        rows.append(SimpleNamespace(
                number=r.number, guest=r.party.rec_name,
                rooms=', '.join(l.room.rec_name for l in r.lines if l.room),
                start=r.start, end=r.end, adults=r.adults,
                children=r.children, state=state_names.get(r.state, r.state),
                amount=r.total_amount))
    return rows


class PrintStart(ModelView):
    "Hotel Report"
    __name__ = 'hotel_pms.report.start'

    company = fields.Many2One('company.company', "Company", required=True)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        domain=[('company', '=', Eval('company', -1))])
    start_date = fields.Date("Start Date", required=True)
    end_date = fields.Date(
        "End Date", required=True,
        domain=[('end_date', '>=', Eval('start_date', None))])

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_hotel(cls):
        company = cls.default_company()
        hotels = Pool().get('hotel.hotel').search(
            [('company', '=', company)], limit=2)
        return hotels[0].id if len(hotels) == 1 else None

    @classmethod
    def default_start_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_end_date(cls):
        return Pool().get('ir.date').today()


class _Print(Wizard):
    "Base of the hotel report wizards (not registered)"
    start = StateView(
        'hotel_pms.report.start',
        'hotel_pms.report_start_view_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Print", 'print_', 'tryton-print', default=True),
            ])
    print_ = StateReport('hotel_pms.report.arrivals')

    def do_print_(self, action):
        return action, {
            'hotel': self.start.hotel.id,
            'start_date': self.start.start_date,
            'end_date': self.start.end_date,
            }

    def transition_print_(self):
        return 'end'


class PrintArrivals(_Print):
    "Print arrivals"
    __name__ = 'hotel_pms.report.print_arrivals'
    print_ = StateReport('hotel_pms.report.arrivals')


class PrintDepartures(_Print):
    "Print departures"
    __name__ = 'hotel_pms.report.print_departures'
    print_ = StateReport('hotel_pms.report.departures')


class PrintOccupancy(_Print):
    "Print occupancy"
    __name__ = 'hotel_pms.report.print_occupancy'
    print_ = StateReport('hotel_pms.report.occupancy')


class PrintRevenue(_Print):
    "Print revenue"
    __name__ = 'hotel_pms.report.print_revenue'
    print_ = StateReport('hotel_pms.report.revenue')


class _PeriodReport(Report):
    "Base of the hotel period reports (not registered)"

    @classmethod
    def get_context(cls, records, header, data):
        pool = Pool()
        context = super().get_context(records, header, data)
        hotel = pool.get('hotel.hotel')(data['hotel'])
        context['hotel'] = hotel
        context['currency'] = hotel.currency
        context['start_date'] = data['start_date']
        context['end_date'] = data['end_date']
        context.update(cls.get_rows(hotel, data['start_date'],
                data['end_date']))
        return context

    @classmethod
    def get_rows(cls, hotel, start, end):
        raise NotImplementedError


class Arrivals(_PeriodReport):
    __name__ = 'hotel_pms.report.arrivals'

    @classmethod
    def get_rows(cls, hotel, start, end):
        return {'rows': movement_rows(hotel, start, end, 'start')}


class Departures(_PeriodReport):
    __name__ = 'hotel_pms.report.departures'

    @classmethod
    def get_rows(cls, hotel, start, end):
        return {'rows': movement_rows(hotel, start, end, 'end')}


class Occupancy(_PeriodReport):
    __name__ = 'hotel_pms.report.occupancy'

    @classmethod
    def get_rows(cls, hotel, start, end):
        rows = occupancy_rows(hotel, start, end)
        return {'rows': rows, 'total': occupancy_total(rows)}


class Revenue(_PeriodReport):
    __name__ = 'hotel_pms.report.revenue'

    @classmethod
    def get_rows(cls, hotel, start, end):
        rows = revenue_rows(hotel, start, end)
        return {
            'rows': rows,
            'total': sum((r.amount for r in rows), _ZERO),
            }


class Folio(Report):
    "Guest folio of a reservation"
    __name__ = 'hotel.reservation.folio'
