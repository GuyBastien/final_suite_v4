# This file is part of the Tryton restaurant_mobile_payment module.
from trytond.pool import Pool

from . import order

__all__ = ['register']


def register():
    Pool.register(
        order.Order,
        order.Payment,
        order.MobileTransaction,
        module='restaurant_mobile_payment', type_='model')
    Pool.register(
        order.Pay,
        module='restaurant_mobile_payment', type_='wizard')
