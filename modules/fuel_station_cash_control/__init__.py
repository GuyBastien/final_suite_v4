# This file is part of the Tryton fuel_station_cash_control module.
from trytond.pool import Pool

from . import cash


def register():
    Pool.register(
        cash.Station,
        cash.Shift,
        cash.ShiftCashCount,
        module='fuel_station_cash_control', type_='model')
