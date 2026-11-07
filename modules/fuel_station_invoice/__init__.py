# This file is part of the Tryton fuel_station_invoice module.
from trytond.pool import Pool

from . import invoice, shift


def register():
    Pool.register(
        shift.Shift,
        invoice.InvoiceLine,
        module='fuel_station_invoice', type_='model')
