# This file is part of the Tryton restaurant module.
from trytond.exceptions import UserWarning
from trytond.model.exceptions import ValidationError


class RestaurantError(ValidationError):
    pass


class OrderError(RestaurantError):
    pass


class ReservationError(RestaurantError):
    pass


class DeliveryError(RestaurantError):
    pass


class RestaurantWarning(UserWarning):
    pass


class CashError(RestaurantError):
    pass
