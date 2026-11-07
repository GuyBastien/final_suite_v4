# This file is part of the Tryton fuel_station module.
from trytond.model.exceptions import ValidationError
from trytond.model.exceptions import ValidationError as _VE  # noqa: F401
from trytond.exceptions import UserWarning


class FuelError(ValidationError):
    pass


class PriceError(FuelError):
    pass


class ShiftError(FuelError):
    pass


class DeliveryError(FuelError):
    pass


class DipError(FuelError):
    pass


class FuelWarning(UserWarning):
    pass


class AuthorizationError(FuelError):
    pass
