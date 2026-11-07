# This file is part of the Tryton hotel module.
from trytond.pool import Pool

from . import article, consumption, hotel, rate, room


def register():
    Pool.register(
        hotel.Hotel,
        room.RoomType,
        room.RoomTypeArticle,
        room.Room,
        rate.Rate,
        article.Template,
        consumption.Consumption,
        consumption.ConsumptionLine,
        consumption.Move,
        module='hotel', type_='model')
