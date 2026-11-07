# This file is part of the Tryton hotel_billing module.
from trytond.pool import Pool

from . import billing


def register():
    Pool.register(
        billing.Party,
        billing.TouristTax,
        billing.Hotel,
        billing.Deposit,
        billing.NightAudit,
        billing.ReservationLine,
        billing.ReservationCharge,
        billing.Reservation,
        module='hotel_billing', type_='model')
