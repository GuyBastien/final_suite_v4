# This file is part of the Tryton sale_point_cash_control module.
from trytond.pool import Pool

from . import cash

__all__ = ['register']


def register():
    Pool.register(
        cash.Denomination,
        cash.CashCount,
        cash.POS,
        cash.CashSession,
        module='sale_point_cash_control', type_='model')
