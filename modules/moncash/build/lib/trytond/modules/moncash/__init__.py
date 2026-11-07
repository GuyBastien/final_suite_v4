# This file is part of the Tryton moncash module.
from trytond.pool import Pool

from . import account

__all__ = ['register']


def register():
    Pool.register(
        account.Account,
        module='moncash', type_='model')
