# This file is part of the Tryton hotel_groups module.
from trytond.model.exceptions import ValidationError


class GroupError(ValidationError):
    pass


class EventError(ValidationError):
    pass
