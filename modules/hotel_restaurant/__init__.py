# This file is part of the Tryton hotel_restaurant module.
from trytond.pool import Pool

from . import restaurant

__all__ = ['register']


def register():
    Pool.register(
        restaurant.Restaurant,
        restaurant.Order,
        restaurant.Payment,
        restaurant.TableReservation,
        restaurant.HotelReservation,
        restaurant.HotelTable,
        restaurant.HotelTableBooking,
        module='hotel_restaurant', type_='model')
