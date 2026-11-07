# This file is part of the Tryton hotel_groups module.
from trytond.pool import Pool

from . import group


def register():
    Pool.register(
        group.Group,
        group.GroupBlock,
        group.EventSpace,
        group.Event,
        group.Reservation,
        group.ReservationLine,
        module='hotel_groups', type_='model')
