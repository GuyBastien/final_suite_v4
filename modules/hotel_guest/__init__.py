# This file is part of the Tryton hotel_guest module.
from trytond.pool import Pool

from . import guest


def register():
    Pool.register(
        guest.Party,
        guest.Hotel,
        guest.LoyaltyProgram,
        guest.LoyaltyEntry,
        guest.GuestRegistration,
        guest.Keycard,
        guest.Reservation,
        module='hotel_guest', type_='model')
