# This file is part of the Tryton hotel_booking_engine module.
from trytond.pool import Pool

from . import booking, web  # noqa: F401


def register():
    Pool.register(
        booking.Hotel,
        booking.Reservation,
        module='hotel_booking_engine', type_='model')
