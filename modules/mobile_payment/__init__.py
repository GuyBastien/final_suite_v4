# This file is part of the Tryton mobile_payment module.
from trytond.pool import Pool

from . import account, push, pos, routes, transaction

__all__ = ['register']


def register():
    Pool.register(
        account.Account,
        account.PaymentMethod,
        push.Push,
        transaction.Transaction,
        transaction.Cron,
        pos.Payment,
        pos.Sale,
        pos.PayStart,
        pos.PayResult,
        module='mobile_payment', type_='model')
    Pool.register(
        pos.Pay,
        module='mobile_payment', type_='wizard')
