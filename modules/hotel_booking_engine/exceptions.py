# This file is part of the Tryton hotel_booking_engine module.
from trytond.model.exceptions import ValidationError


class BookingError(ValidationError):
    pass


class BookingUnavailableError(BookingError):
    "The requested rooms are no longer available"
