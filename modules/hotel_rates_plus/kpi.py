# This file is part of the Tryton hotel_rates_plus module.
import datetime
from decimal import Decimal

from trytond.model import ModelSQL, ModelView, fields
from trytond.modules.hotel_pms import report
from trytond.pool import Pool
from trytond.pyson import Eval

_ZERO = Decimal(0)
_DIGITS = (16, Eval('currency_digits', 2))
FORECAST_DAYS = 30


class RevenueKPI(ModelSQL, ModelView):
    """Revenue indicators of a hotel.

    One row per hotel: today, month to date and forecast of the next 30
    days (confirmed and checked-in reservations). Figures are computed when
    the record is read.
    """
    __name__ = 'hotel.revenue.kpi'
    _rec_name = 'name'

    name = fields.Char("Name", readonly=True)
    hotel = fields.Many2One('hotel.hotel', "Hotel", readonly=True)
    company = fields.Many2One('company.company', "Company", readonly=True)
    currency_digits = fields.Function(
        fields.Integer("Currency Digits"), 'get_kpi')
    rooms_total = fields.Function(fields.Integer("Rooms"), 'get_kpi')

    occupancy_today = fields.Function(
        fields.Numeric("Occupancy Today (%)", digits=(16, 1)), 'get_kpi')
    revenue_today = fields.Function(
        fields.Numeric("Room Revenue Today", digits=_DIGITS), 'get_kpi')
    adr_today = fields.Function(
        fields.Numeric("ADR Today", digits=_DIGITS), 'get_kpi')
    revpar_today = fields.Function(
        fields.Numeric("RevPAR Today", digits=_DIGITS), 'get_kpi')

    rooms_available = fields.Function(
        fields.Integer("Room-Nights Available (MTD)"), 'get_kpi')
    rooms_sold = fields.Function(
        fields.Integer("Room-Nights Sold (MTD)"), 'get_kpi')
    occupancy_mtd = fields.Function(
        fields.Numeric("Occupancy MTD (%)", digits=(16, 1)), 'get_kpi')
    room_revenue = fields.Function(
        fields.Numeric("Room Revenue (MTD)", digits=_DIGITS), 'get_kpi')
    adr = fields.Function(
        fields.Numeric("ADR (MTD)", digits=_DIGITS), 'get_kpi')
    revpar = fields.Function(
        fields.Numeric("RevPAR (MTD)", digits=_DIGITS), 'get_kpi')

    forecast_sold = fields.Function(
        fields.Integer("Room-Nights Sold (Next 30 Days)"), 'get_kpi')
    forecast_occupancy = fields.Function(
        fields.Numeric("Forecast Occupancy (%)", digits=(16, 1)), 'get_kpi')
    forecast_revenue = fields.Function(
        fields.Numeric("Forecast Revenue", digits=_DIGITS), 'get_kpi')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('hotel', 'ASC')]

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
    def get_kpi(cls, records, names):
        result = {name: {} for name in names}
        for record in records:
            values = record._compute()
            for name in names:
                result[name][record.id] = values[name]
        return result

    def _compute(self):
        hotel = self.hotel
        today = datetime.date.today()
        values = {'currency_digits': hotel.currency.digits}
        day = report.occupancy_rows(hotel, today, today)[0]
        values.update(
            rooms_total=day.rooms, occupancy_today=day.rate,
            revenue_today=day.revenue, adr_today=day.adr,
            revpar_today=day.revpar)
        month = report.occupancy_total(report.occupancy_rows(
                hotel, today.replace(day=1), today))
        values.update(
            rooms_available=month.rooms, rooms_sold=month.occupied,
            occupancy_mtd=month.rate, room_revenue=month.revenue,
            adr=month.adr, revpar=month.revpar)
        forecast = report.occupancy_total(report.occupancy_rows(
                hotel, today,
                today + datetime.timedelta(days=FORECAST_DAYS - 1)))
        values.update(
            forecast_sold=forecast.occupied,
            forecast_occupancy=forecast.rate,
            forecast_revenue=forecast.revenue)
        return values
