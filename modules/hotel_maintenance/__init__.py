# This file is part of the Tryton hotel_maintenance module.
from trytond.pool import Pool

from . import maintenance


def register():
    Pool.register(
        maintenance.Hotel,
        maintenance.Order,
        maintenance.Schedule,
        maintenance.LostItem,
        maintenance.LaundryBatch,
        maintenance.LaundryBatchLine,
        maintenance.Cron,
        maintenance.Move,
        module='hotel_maintenance', type_='model')
