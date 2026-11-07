# This file is part of the Tryton hotel_mobile_payment module.
from trytond.pool import Pool

from . import reservation

__all__ = ['register']


def register():
    Pool.register(
        reservation.Reservation,
        reservation.Deposit,
        reservation.MobileTransaction,
        module='hotel_mobile_payment', type_='model')
    Pool.register(
        reservation.Pay,
        module='hotel_mobile_payment', type_='wizard')
