# This file is part of the Tryton sale_point_cash_control module.
from trytond.exceptions import UserWarning
from trytond.model.exceptions import ValidationError


class CashControlError(ValidationError):
    pass


class CashControlWarning(UserWarning):
    pass
