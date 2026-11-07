# This file is part of the Tryton hotel_pms module.
import datetime
from decimal import Decimal

from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval

from . import report

_ZERO = Decimal(0)
_ROOM_STATES = {
    'rooms_available': 'available',
    'rooms_occupied': 'occupied',
    'rooms_dirty': 'dirty',
    'rooms_maintenance': 'maintenance',
    'rooms_out_of_order': 'out_of_order',
    }


def _day_bounds(day):
    return (datetime.datetime.combine(day, datetime.time.min),
        datetime.datetime.combine(day, datetime.time.max))


class Dashboard(ModelSQL, ModelView):
    """Live figures of a hotel.

    One row per hotel; every value is computed when the record is read, so
    the dashboard is up to date each time it is opened or reloaded.
    """
    __name__ = 'hotel.dashboard'
    _rec_name = 'name'

    name = fields.Char("Name", readonly=True)
    hotel = fields.Many2One('hotel.hotel', "Hotel", readonly=True)
    company = fields.Many2One('company.company', "Company", readonly=True)
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'get_stats')

    rooms_total = fields.Function(fields.Integer("Rooms"), 'get_stats')
    rooms_available = fields.Function(
        fields.Integer("Available"), 'get_stats')
    rooms_occupied = fields.Function(fields.Integer("Occupied"), 'get_stats')
    rooms_dirty = fields.Function(fields.Integer("Dirty"), 'get_stats')
    rooms_maintenance = fields.Function(
        fields.Integer("Maintenance"), 'get_stats')
    rooms_out_of_order = fields.Function(
        fields.Integer("Out of Order"), 'get_stats')
    occupancy_rate = fields.Function(
        fields.Numeric("Occupancy (%)", digits=(16, 1)), 'get_stats')

    arrivals_today = fields.Function(
        fields.Integer("Arrivals Today"), 'get_stats')
    arrivals_left = fields.Function(
        fields.Integer("Arrivals Left"), 'get_stats')
    departures_today = fields.Function(
        fields.Integer("Departures Today"), 'get_stats')
    departures_left = fields.Function(
        fields.Integer("Departures Left"), 'get_stats')
    in_house = fields.Function(fields.Integer("In House"), 'get_stats')
    guests_in_house = fields.Function(
        fields.Integer("Guests In House"), 'get_stats')

    revenue_today = fields.Function(
        fields.Numeric("Room Revenue Today",
            digits=(16, Eval('currency_digits', 2))), 'get_stats')
    revenue_month = fields.Function(
        fields.Numeric("Room Revenue This Month",
            digits=(16, Eval('currency_digits', 2))), 'get_stats')
    adr = fields.Function(
        fields.Numeric("ADR", digits=(16, Eval('currency_digits', 2))), 'get_stats')
    revpar = fields.Function(
        fields.Numeric("RevPAR", digits=(16, Eval('currency_digits', 2))), 'get_stats')
    pos_pending = fields.Function(
        fields.Numeric("POS Charges Pending",
            digits=(16, Eval('currency_digits', 2))), 'get_stats')

    open_alerts = fields.Function(fields.Integer("Alerts"), 'get_stats')
    open_warnings = fields.Function(fields.Integer("Warnings"), 'get_stats')
    open_reminders = fields.Function(
        fields.Integer("Reminders"), 'get_stats')

    arrivals = fields.Function(
        fields.One2Many('hotel.reservation', None, "Arrivals"),
        'get_lists')
    departures = fields.Function(
        fields.One2Many('hotel.reservation', None, "Departures"),
        'get_lists')
    in_house_reservations = fields.Function(
        fields.One2Many('hotel.reservation', None, "In House"),
        'get_lists')
    notifications = fields.Function(
        fields.One2Many('hotel.notification', None, "Notifications"),
        'get_lists')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('hotel', 'ASC')]
        cls._buttons.update({
                'refresh': {'icon': 'tryton-refresh'},
                })

    @classmethod
    def table_query(cls):
        hotel = Pool().get('hotel.hotel').__table__()
        return hotel.select(
            hotel.id.as_('id'),
            hotel.create_uid.as_('create_uid'),
            hotel.create_date.as_('create_date'),
            hotel.write_uid.as_('write_uid'),
            hotel.write_date.as_('write_date'),
            hotel.name.as_('name'),
            hotel.id.as_('hotel'),
            hotel.company.as_('company'),
            where=hotel.active)

    @classmethod
    @ModelView.button
    def refresh(cls, dashboards):
        "Raise the notifications and reload the figures"
        Notification = Pool().get('hotel.notification')
        Notification.generate([d.hotel for d in dashboards])

    # Figures

    @classmethod
    def get_stats(cls, dashboards, names):
        result = {name: {} for name in names}
        for dashboard in dashboards:
            values = dashboard._compute_stats()
            for name in names:
                result[name][dashboard.id] = values[name]
        return result

    def _compute_stats(self):
        pool = Pool()
        Room = pool.get('hotel.room')
        Reservation = pool.get('hotel.reservation')
        Notification = pool.get('hotel.notification')
        hotel = self.hotel
        now = datetime.datetime.now()
        today = now.date()
        low, high = _day_bounds(today)
        values = {'currency_digits': hotel.currency.digits}

        base = [('hotel', '=', hotel.id)]
        values['rooms_total'] = Room.search_count(base)
        for name, state in _ROOM_STATES.items():
            values[name] = Room.search_count(base + [('state', '=', state)])
        values['occupancy_rate'] = (
            Decimal(values['rooms_occupied']) * 100 / values['rooms_total']
            if values['rooms_total'] else _ZERO)

        arrivals = Reservation.search_count(base + [
                ('state', 'in', ['confirmed', 'checked_in', 'checked_out']),
                ('start', '>=', low), ('start', '<=', high)])
        values['arrivals_today'] = arrivals
        values['arrivals_left'] = Reservation.search_count(base + [
                ('state', '=', 'confirmed'),
                ('start', '>=', low), ('start', '<=', high)])
        values['departures_today'] = Reservation.search_count(base + [
                ('state', 'in', ['checked_in', 'checked_out']),
                ('end', '>=', low), ('end', '<=', high)])
        values['departures_left'] = Reservation.search_count(base + [
                ('state', '=', 'checked_in'),
                ('end', '>=', low), ('end', '<=', high)])
        in_house = Reservation.search(base + [('state', '=', 'checked_in')])
        values['in_house'] = len(in_house)
        values['guests_in_house'] = sum(
            r.adults + r.children for r in in_house)

        today_row = report.occupancy_rows(hotel, today, today)[0]
        values['revenue_today'] = today_row.revenue
        values['adr'] = today_row.adr
        values['revpar'] = today_row.revpar
        month_start = today.replace(day=1)
        values['revenue_month'] = sum(
            (r.revenue for r in report.occupancy_rows(
                    hotel, month_start, today)), _ZERO)
        pending = _ZERO
        for reservation in in_house:
            pending += reservation.pos_amount or _ZERO
        values['pos_pending'] = pending

        open_ = base + [('state', '=', 'open')]
        for kind, name in [('alert', 'open_alerts'),
                ('warning', 'open_warnings'),
                ('reminder', 'open_reminders')]:
            values[name] = Notification.search_count(
                open_ + [('kind', '=', kind)])
        return values

    @classmethod
    def get_lists(cls, dashboards, names):
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        Notification = pool.get('hotel.notification')
        today = datetime.date.today()
        low, high = _day_bounds(today)
        result = {name: {} for name in names}
        for dashboard in dashboards:
            base = [('hotel', '=', dashboard.hotel.id)]
            domains = {
                'arrivals': (Reservation, base + [
                        ('state', 'in', ['confirmed', 'checked_in']),
                        ('start', '>=', low), ('start', '<=', high)],
                    [('start', 'ASC')]),
                'departures': (Reservation, base + [
                        ('state', '=', 'checked_in'),
                        ('end', '>=', low), ('end', '<=', high)],
                    [('end', 'ASC')]),
                'in_house_reservations': (Reservation, base + [
                        ('state', '=', 'checked_in')], [('end', 'ASC')]),
                'notifications': (Notification, base + [
                        ('state', '=', 'open')],
                    [('date', 'DESC')]),
                }
            for name in names:
                model, domain, order = domains[name]
                result[name][dashboard.id] = [
                    r.id for r in model.search(domain, order=order)]
        return result
