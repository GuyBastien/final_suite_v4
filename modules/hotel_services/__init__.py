# This file is part of the Tryton hotel_services module.
from trytond.pool import Pool

from . import service


def register():
    Pool.register(
        service.Resource,
        service.Service,
        service.Booking,
        service.Table,
        service.TableBooking,
        module='hotel_services', type_='model')
