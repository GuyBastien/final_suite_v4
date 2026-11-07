# This file is part of the Tryton hotel_channel_manager module.
from trytond.pool import Pool

from . import channel, web  # noqa: F401


def register():
    Pool.register(
        channel.Channel,
        channel.Mapping,
        channel.Booking,
        channel.Log,
        channel.Reservation,
        channel.Cron,
        channel.Notification,
        module='hotel_channel_manager', type_='model')
