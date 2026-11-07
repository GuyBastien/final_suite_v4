# This file is part of the Tryton hotel_housekeeping module.
from trytond.pool import Pool

from . import housekeeping


def register():
    Pool.register(
        housekeeping.Hotel,
        housekeeping.ChecklistTemplate,
        housekeeping.Task,
        housekeeping.TaskItem,
        housekeeping.Issue,
        housekeeping.Cron,
        housekeeping.Reservation,
        housekeeping.Notification,
        module='hotel_housekeeping', type_='model')
