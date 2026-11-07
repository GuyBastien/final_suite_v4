# This file is part of the Tryton fuel_station module.
from trytond.pool import Pool

from . import (
    alert, authorization, guide, delivery, dip, meter, price, product, report, shift, shop,
    station)


def register():
    Pool.register(
        product.Template,
        station.Station,
        alert.Station,
        station.Tank,
        alert.Tank,
        station.Pump,
        station.Nozzle,
        price.Price,
        shift.Shift,
        alert.Shift,
        alert.Cron,
        shift.ShiftLine,
        shift.ShiftPayment,
        shop.ShiftSale,
        authorization.Station,
        authorization.Authorization,
        authorization.AuthorizationLog,
        authorization.Cron,
        guide.GuideStart,
        meter.MeterReading,
        meter.ImportReadingsStart,
        delivery.Delivery,
        delivery.DeliveryLine,
        dip.Dip,
        report.SalesReport,
        report.Dashboard,
        shift.Move,
        module='fuel_station', type_='model')
    Pool.register(
        report.ShiftReport,
        module='fuel_station', type_='report')
    Pool.register(
        guide.Guide,
        meter.ImportReadings,
        module='fuel_station', type_='wizard')
