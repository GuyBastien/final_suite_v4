# This file is part of the Tryton hotel_billing module.
from trytond.model.exceptions import ValidationError


class BillingError(ValidationError):
    pass
