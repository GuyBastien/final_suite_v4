# This file is part of the Tryton natcash module.
from trytond.pool import Pool

from . import account

__all__ = ['register']


def register():
    Pool.register(
        account.Account,
        module='natcash', type_='model')
