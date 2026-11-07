# This file is part of the Tryton fuel_station_sale_point module.
from trytond.pool import Pool

from . import sale_point


def register():
    Pool.register(
        sale_point.Station,
        sale_point.Shift,
        module='fuel_station_sale_point', type_='model')
