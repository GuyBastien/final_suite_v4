# This file is part of the Tryton fuel_station module.
from decimal import Decimal

from sql.aggregate import Max, Min, Sum

from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.report import Report
from trytond.transaction import Transaction

_ZERO = Decimal(0)


class SalesReport(ModelSQL, ModelView):
    "Fuel Sales by Day"
    __name__ = 'fuel.report.sales'

    company = fields.Many2One('company.company', "Company", readonly=True,
        help="Company that owns the record.")
    station = fields.Many2One('fuel.station', "Station", readonly=True,
        help="Station concerned.")
    date = fields.Date("Date", readonly=True,
        help="Date of the document.")
    product = fields.Many2One(
        'product.product', "Fuel", readonly=True,
        context={'company': Eval('company', -1)},
        help="Product concerned.")
    quantity = fields.Float("Gallons Sold", digits=(16, 3), readonly=True,
        help="Quantity, in the unit of measure.")
    amount = fields.Numeric("Sales", digits=(16, 2), readonly=True,
        help="Amount in the currency of the document.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('station', 'ASC'))

    @classmethod
    def table_query(cls):
        pool = Pool()
        Line = pool.get('fuel.shift.line')
        Shift = pool.get('fuel.shift')
        Nozzle = pool.get('fuel.nozzle')
        Tank = pool.get('fuel.tank')
        line = Line.__table__()
        shift = Shift.__table__()
        nozzle = Nozzle.__table__()
        tank = Tank.__table__()
        return (line
            .join(shift, condition=line.shift == shift.id)
            .join(nozzle, condition=line.nozzle == nozzle.id)
            .join(tank, condition=nozzle.tank == tank.id)
            .select(
                Min(line.id).as_('id'),
                Min(line.create_uid).as_('create_uid'),
                Min(line.create_date).as_('create_date'),
                Max(line.write_uid).as_('write_uid'),
                Max(line.write_date).as_('write_date'),
                shift.company.as_('company'),
                shift.station.as_('station'),
                shift.date.as_('date'),
                tank.product.as_('product'),
                Sum(line.quantity).as_('quantity'),
                Sum(line.amount).as_('amount'),
                where=shift.state == 'closed',
                group_by=[shift.company, shift.station, shift.date,
                    tank.product]))


class Dashboard(ModelSQL, ModelView):
    "Fuel Station Dashboard"
    __name__ = 'fuel.dashboard'
    _rec_name = 'name'

    name = fields.Char("Name", readonly=True,
        help="Name displayed in lists and searches.")
    station = fields.Many2One('fuel.station', "Station", readonly=True,
        help="Station concerned.")
    company = fields.Many2One('company.company', "Company", readonly=True,
        help="Company that owns the record.")
    tanks = fields.Function(fields.Integer("Tanks",
        help="Tanks of the station."), 'get_stats')
    low_tanks = fields.Function(fields.Integer("Low Tanks",
        help="Tanks under their minimum level."), 'get_stats')
    open_shifts = fields.Function(fields.Integer("Open Shifts",
        help="Shifts still open."), 'get_stats')
    quantity_today = fields.Function(
        fields.Float("Gallons Today", digits=(16, 3),
        help="Volume sold today."), 'get_stats')
    sales_today = fields.Function(
        fields.Numeric("Sales Today", digits=(16, 2),
        help="Sales of the day."), 'get_stats')
    deliveries_month = fields.Function(
        fields.Float("Delivered This Month", digits=(16, 3),
        help="Volume received by the tanks this month."), 'get_stats')

    @classmethod
    def table_query(cls):
        station = Pool().get('fuel.station').__table__()
        return station.select(
            station.id.as_('id'),
            station.create_uid.as_('create_uid'),
            station.create_date.as_('create_date'),
            station.write_uid.as_('write_uid'),
            station.write_date.as_('write_date'),
            station.name.as_('name'),
            station.id.as_('station'),
            station.company.as_('company'),
            where=station.active)

    @classmethod
    def get_stats(cls, dashboards, names):
        pool = Pool()
        Tank = pool.get('fuel.tank')
        Shift = pool.get('fuel.shift')
        Delivery = pool.get('fuel.delivery')
        today = pool.get('ir.date').today()
        result = {name: {} for name in names}
        for dashboard in dashboards:
            station = dashboard.station
            tanks = Tank.search([('station', '=', station.id)])
            quantities = Tank.quantities(tanks)
            shifts = Shift.search([
                    ('station', '=', station.id), ('date', '=', today),
                    ('state', '=', 'closed')])
            deliveries = Delivery.search([
                    ('station', '=', station.id), ('state', '=', 'done'),
                    ('date', '>=', today.replace(day=1))])
            values = {
                'tanks': len(tanks),
                'low_tanks': sum(
                    1 for t in tanks
                    if quantities.get(t.id, 0.) < t.min_level),
                'open_shifts': Shift.search_count([
                        ('station', '=', station.id), ('state', '=', 'open')]),
                'quantity_today': sum(s.total_quantity for s in shifts),
                'sales_today': sum((s.total_sales for s in shifts), _ZERO),
                'deliveries_month': sum(d.total_quantity for d in deliveries),
                }
            for name in names:
                result[name][dashboard.id] = values[name]
        return result


class ShiftReport(Report):
    "Shift closing sheet"
    __name__ = 'fuel.shift.report'
