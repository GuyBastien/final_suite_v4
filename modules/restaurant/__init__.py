# This file is part of the Tryton restaurant module.
from trytond.pool import Pool

from . import (
    cash, delivery, guide, menu, order, report, reservation, restaurant, screen)


def register():
    Pool.register(
        menu.Template,
        menu.Recipe,
        menu.RecipeLine,
        menu.HappyHour,
        menu.Menu,
        menu.MenuItem,
        restaurant.Restaurant,
        restaurant.Area,
        restaurant.Table,
        cash.CashSession,
        cash.CashCount,
        order.Order,
        order.OrderLine,
        order.Payment,
        order.Move,
        screen.Restaurant,
        screen.Template,
        screen.OrderLine,
        screen.Cron,
        reservation.Reservation,
        delivery.Driver,
        report.SalesReport,
        report.Dashboard,
        guide.GuideStart,
        module='restaurant', type_='model')
    Pool.register(
        report.OrderReceipt,
        report.OrderTicket,
        report.OrderSplitBill,
        module='restaurant', type_='report')
    Pool.register(
        guide.Guide,
        module='restaurant', type_='wizard')
