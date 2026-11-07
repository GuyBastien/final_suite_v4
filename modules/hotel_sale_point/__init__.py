# This file is part of the Tryton hotel_sale_point module.
from trytond.pool import Pool

from . import pos

__all__ = ['register']


def register():
    Pool.register(
        pos.Sale,
        module='hotel_sale_point', type_='model')
