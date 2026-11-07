# This file is part of the Tryton sale_point_quick module.
from trytond.pool import Pool

from . import pos

__all__ = ['register']


def register():
    Pool.register(
        pos.Point,
        pos.Template,
        pos.Sale,
        module='sale_point_quick', type_='model')
