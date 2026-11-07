# This file is part of the Tryton hotel_pms module.
from trytond.pool import Pool

from . import (
    consumption, dashboard, doc, notification, pos, purchase, report,
    reservation, room)


def register():
    Pool.register(
        consumption.Consumption,
        purchase.Hotel,
        pos.Hotel,
        pos.PaymentMethod,
        pos.POSSale,
        pos.POSPayment,
        room.RoomType,
        room.Room,
        reservation.Reservation,
        reservation.ReservationLine,
        reservation.ReservationCharge,
        reservation.Sale,
        notification.Hotel,
        notification.Notification,
        notification.Cron,
        notification.Reservation,
        dashboard.Dashboard,
        doc.Documentation,
        report.PrintStart,
        module='hotel_pms', type_='model')
    Pool.register(
        report.PrintArrivals,
        report.PrintDepartures,
        report.PrintOccupancy,
        report.PrintRevenue,
        module='hotel_pms', type_='wizard')
    Pool.register(
        report.Arrivals,
        report.Departures,
        report.Occupancy,
        report.Revenue,
        report.Folio,
        module='hotel_pms', type_='report')
