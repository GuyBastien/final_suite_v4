# This file is part of the Tryton fuel_station_invoice module.
from trytond.pool import PoolMeta


class InvoiceLine(metaclass=PoolMeta):
    __name__ = 'account.invoice.line'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['fuel.shift']
