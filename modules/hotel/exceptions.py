# This file is part of the Tryton hotel module.
from trytond.model.exceptions import ValidationError


class RateError(ValidationError):
    pass


class ConsumptionError(ValidationError):
    pass
