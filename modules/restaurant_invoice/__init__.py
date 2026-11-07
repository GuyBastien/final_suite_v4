# This file is part of the Tryton restaurant_invoice module.
from trytond.pool import Pool

from . import invoice, order


def register():
    Pool.register(
        order.Restaurant,
        order.Order,
        invoice.InvoiceLine,
        module='restaurant_invoice', type_='model')
