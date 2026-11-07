# This file is part of the Tryton fuel_station module.
from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval

from .exceptions import FuelError
from .tools import DEFAULT_DENSITY, FUEL_FAMILIES


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    fuel = fields.Boolean(
        "Fuel", states={'invisible': Eval('type') != 'goods'},
        help="Check for products sold at the pumps (petrol, diesel, "
        "kerosene, LPG...). Only these products can be stored in tanks. "
        "Their unit of measure is the gallon.")
    fuel_family = fields.Selection(
        FUEL_FAMILIES, "Fuel Family",
        states={'invisible': ~Eval('fuel', False),
            'required': Eval('fuel', False)},
        help="Used for the temperature correction of volumes.")
    fuel_density = fields.Float(
        "Density at 15 °C", digits=(16, 1),
        states={'invisible': ~Eval('fuel', False)},
        domain=[('fuel_density', '>=', 0)],
        help="Density in kg/m³ at 15 °C. Used for the temperature "
        "correction of volumes.")

    @classmethod
    def default_fuel(cls):
        return False

    @classmethod
    def default_fuel_family(cls):
        return 'other'

    @classmethod
    def default_fuel_density(cls):
        return 0.0

    @staticmethod
    def _gallon():
        Uom = Pool().get('product.uom')
        gallons = Uom.search([('symbol', '=', 'gal')], limit=1)
        return gallons[0] if gallons else None

    @fields.depends('fuel', 'default_uom')
    def on_change_fuel(self):
        if self.fuel:
            gallon = self._gallon()
            if gallon and (
                    not self.default_uom
                    or self.default_uom.category != gallon.category):
                self.default_uom = gallon

    @fields.depends('fuel', 'fuel_family', 'fuel_density')
    def on_change_fuel_family(self):
        if self.fuel and not self.fuel_density:
            self.fuel_density = DEFAULT_DENSITY.get(self.fuel_family, 0.0)

    @classmethod
    def validate(cls, templates):
        super().validate(templates)
        for template in templates:
            if template.fuel:
                gallon = cls._gallon()
                if gallon and template.default_uom.category != gallon.category:
                    raise FuelError(gettext(
                            'fuel_station.msg_fuel_uom',
                            product=template.rec_name))
