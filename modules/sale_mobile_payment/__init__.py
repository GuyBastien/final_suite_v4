# This file is part of the Tryton sale_mobile_payment module.
from trytond.pool import Pool

from . import invoice

__all__ = ['register']


def register():
    Pool.register(
        invoice.Account,
        invoice.MobileTransaction,
        invoice.Invoice,
        invoice.Sale,
        module='sale_mobile_payment', type_='model')
    Pool.register(
        invoice.Pay,
        module='sale_mobile_payment', type_='wizard')
