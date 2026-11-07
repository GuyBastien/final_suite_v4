# This file is part of the Tryton party_person_ht module.
from trytond.pool import Pool

from . import country, party

__all__ = ['register']


def register():
    Pool.register(
        party.Party,
        party.Identifier,
        party.Configuration,
        country.Subdivision,
        module='party_person_ht', type_='model')
