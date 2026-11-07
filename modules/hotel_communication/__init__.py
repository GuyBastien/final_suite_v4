# This file is part of the Tryton hotel_communication module.
from trytond.pool import Pool

from . import message


def register():
    Pool.register(
        message.Hotel,
        message.Template,
        message.Message,
        message.Cron,
        message.Reservation,
        module='hotel_communication', type_='model')
    # Public pre-check-in route
    from . import web  # noqa: F401
