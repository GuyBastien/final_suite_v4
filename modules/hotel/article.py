# This file is part of the Tryton hotel module.
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval

HOTEL_CATEGORIES = [
    (None, ""),
    ('minibar', "Minibar"),
    ('amenity', "Guest Amenity"),
    ('food_beverage', "Food & Beverage"),
    ('linen', "Linen"),
    ('cleaning', "Cleaning Supply"),
    ('maintenance', "Maintenance Supply"),
    ('equipment', "Equipment"),
    ]


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    hotel_article = fields.Boolean(
        "Hotel Article",
        help="Check to manage this product as a hotel article or "
        "consumable (stocked in the hotel warehouse and rooms).")
    hotel_category = fields.Selection(
        HOTEL_CATEGORIES, "Hotel Category",
        states={
            'invisible': ~Eval('hotel_article', False),
            'required': Eval('hotel_article', False),
            })
    hotel_billable = fields.Boolean(
        "Billed to Guest",
        states={
            'invisible': ~Eval('hotel_article', False),
            },
        help="Check if its consumption in a room is charged to the guest "
        "(e.g. minibar items).")

    @classmethod
    def default_hotel_article(cls):
        return False

    @classmethod
    def default_hotel_billable(cls):
        return False
