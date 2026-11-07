# This file is part of the Tryton fuel_station_sale module.
from trytond.pool import Pool

from . import authorization


def register():
    Pool.register(
        authorization.Template,
        authorization.Station,
        authorization.Authorization,
        module='fuel_station_sale', type_='model')
