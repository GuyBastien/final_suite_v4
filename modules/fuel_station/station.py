# This file is part of the Tryton fuel_station module.
from collections import defaultdict

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Check, Unique, fields)
from trytond.pool import Pool
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

from .exceptions import FuelError


class Station(DeactivableMixin, ModelSQL, ModelView):
    "Fuel Station"
    __name__ = 'fuel.station'
    _rec_name = 'name'

    name = fields.Char("Name", required=True, translate=True,
        help="Name displayed in lists and searches.")
    code = fields.Char("Code", required=True,
        help="Short unique identifier.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        help="Company that owns the record.")
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency",
        help="Currency of the amounts."),
        'on_change_with_currency')
    party = fields.Many2One(
        'party.party', "Contact",
        context={'company': Eval('company', -1)},
        help="Address and phone number of the station.")
    warehouse = fields.Many2One(
        'stock.location', "Warehouse", required=True,
        domain=[('type', '=', 'warehouse')],
        help="The tanks are stock locations created under the storage "
        "location of this warehouse.")
    cash_tolerance = fields.Numeric(
        "Cash Tolerance", digits='currency', required=True,
        domain=[('cash_tolerance', '>=', 0)],
        help="A shift whose cash difference exceeds this amount asks "
        "for a confirmation when it is closed.")
    dip_tolerance = fields.Float(
        "Dip Tolerance (%)", required=True,
        domain=[('dip_tolerance', '>=', 0)],
        help="A dip whose variance exceeds this percentage of the book "
        "quantity asks for a confirmation.")
    temperature_correction = fields.Boolean(
        "Book at 15 °C",
        help="Deliveries and dips are booked in the stock at the volume "
        "corrected to 15 °C when the temperature is entered.")
    tanks = fields.One2Many('fuel.tank', 'station', "Tanks",
        help="Tanks of the station.")
    pumps = fields.One2Many('fuel.pump', 'station', "Pumps",
        help="Pumps of the station.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_company_unique', Unique(t, t.company, t.code),
                'fuel_station.msg_station_code_unique'),
            ]
        cls._order.insert(0, ('code', 'ASC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_temperature_correction(cls):
        return False

    @classmethod
    def default_cash_tolerance(cls):
        from decimal import Decimal
        return Decimal(0)

    @classmethod
    def default_dip_tolerance(cls):
        return 1.0

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id

    def get_rec_name(self, name):
        return "[%s] %s" % (self.code, self.name)

    @classmethod
    def search_rec_name(cls, name, clause):
        _, operator, operand = clause[:3]
        if operator.startswith('!') or operator.startswith('not '):
            bool_op = 'AND'
        else:
            bool_op = 'OR'
        return [bool_op,
            ('code', operator, operand) + tuple(clause[3:]),
            ('name', operator, operand) + tuple(clause[3:]),
            ]


class Tank(DeactivableMixin, ModelSQL, ModelView):
    "Fuel Tank"
    __name__ = 'fuel.tank'

    station = fields.Many2One(
        'fuel.station', "Station", required=True, ondelete='CASCADE',
        help="Station concerned.")
    code = fields.Char("Code", required=True,
        help="Short unique identifier.")
    name = fields.Char("Name",
        help="Name displayed in lists and searches.")
    product = fields.Many2One(
        'product.product', "Fuel", required=True,
        domain=[('template.fuel', '=', True), ('type', '=', 'goods')],
        states={'readonly': Eval('id', -1) >= 0},
        help="The fuel stored in the tank (cannot change after creation).")
    unit = fields.Function(
        fields.Many2One('product.uom', "Unit",
        help="Unit of measure."), 'on_change_with_unit')
    location = fields.Many2One(
        'stock.location', "Stock Location", readonly=True,
        domain=[('type', '=', 'storage')],
        help="Created automatically under the storage location of the "
        "warehouse of the station.")
    capacity = fields.Float(
        "Capacity", digits='unit', required=True,
        domain=[('capacity', '>', 0)],
        help="Maximum volume the tank can hold.")
    min_level = fields.Float(
        "Minimum Level", digits='unit', required=True,
        domain=[('min_level', '>=', 0)],
        help="Below this quantity the tank is flagged as low.")
    quantity = fields.Function(
        fields.Float("Quantity", digits='unit',
        help="Quantity, in the unit of measure."), 'get_quantity')
    fill_ratio = fields.Function(
        fields.Float("Filling", digits=(16, 4),
        help="Percentage of the capacity filled."), 'get_quantity')
    low = fields.Function(
        fields.Boolean("Low",
        help="Checked when the level is under the minimum."), 'get_quantity', searcher='search_low')
    free_space = fields.Function(
        fields.Float("Free Space", digits='unit',
        help="Volume that can still be received."), 'get_quantity')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_station_unique', Unique(t, t.station, t.code),
                'fuel_station.msg_tank_code_unique'),
            ('min_capacity', Check(t, t.min_level <= t.capacity),
                'fuel_station.msg_tank_min_capacity'),
            ]
        cls._order.insert(0, ('station', 'ASC'))
        cls._order.insert(1, ('code', 'ASC'))

    @classmethod
    def default_min_level(cls):
        return 0.

    @fields.depends('product')
    def on_change_with_unit(self, name=None):
        if self.product:
            return self.product.default_uom.id

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(Eval('low'), 'danger', '')),
            ]

    def get_rec_name(self, name):
        text = "%s / %s" % (self.station.code, self.code)
        if self.product:
            text += " - %s" % self.product.rec_name
        return text

    @classmethod
    def search_rec_name(cls, name, clause):
        _, operator, operand = clause[:3]
        if operator.startswith('!') or operator.startswith('not '):
            bool_op = 'AND'
        else:
            bool_op = 'OR'
        return [bool_op,
            ('code', operator, operand) + tuple(clause[3:]),
            ('name', operator, operand) + tuple(clause[3:]),
            ('product.rec_name', operator, operand) + tuple(clause[3:]),
            ('station.code', operator, operand) + tuple(clause[3:]),
            ]

    # Stock

    @classmethod
    def quantities(cls, tanks):
        "Return {tank id: book quantity} from the done stock moves"
        Product = Pool().get('product.product')
        result = {}
        by_product = defaultdict(list)
        for tank in tanks:
            if tank.location:
                by_product[tank.product.id].append(tank)
            else:
                result[tank.id] = 0.
        locations = [t.location.id for t in tanks if t.location]
        if not locations:
            return result
        with Transaction().set_context(stock_date_end=None):
            found = Product.products_by_location(
                locations, with_childs=False,
                grouping_filter=(list(by_product),))
        for product_id, group in by_product.items():
            for tank in group:
                result[tank.id] = found.get(
                    (tank.location.id, product_id), 0.)
        return result

    @classmethod
    def get_quantity(cls, tanks, names):
        quantities = cls.quantities(tanks)
        result = {name: {} for name in names}
        for tank in tanks:
            qty = quantities.get(tank.id, 0.)
            values = {
                'quantity': qty,
                'fill_ratio': max(0., min(1., qty / tank.capacity))
                if tank.capacity else 0.,
                'low': qty < tank.min_level,
                'free_space': max(0., tank.capacity - qty),
                }
            for name in names:
                result[name][tank.id] = values[name]
        return result

    @classmethod
    def search_low(cls, name, clause):
        _, operator, value = clause[:3]
        wanted = bool(value) if operator in ('=', '==') else not bool(value)
        tanks = cls.search([])
        quantities = cls.quantities(tanks)
        ids = [t.id for t in tanks
            if (quantities.get(t.id, 0.) < t.min_level) == wanted]
        return [('id', 'in', ids)]

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Station = pool.get('fuel.station')
        Location = pool.get('stock.location')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('location') and values.get('station'):
                station = Station(values['station'])
                parent = station.warehouse.storage_location
                if parent is None:
                    raise FuelError(gettext(
                            'fuel_station.msg_station_no_storage',
                            station=station.rec_name))
                location, = Location.create([{
                            'name': "%s %s" % (
                                station.code, values.get('code', '')),
                            'code': values.get('code'),
                            'type': 'storage',
                            'parent': parent.id,
                            }])
                values['location'] = location.id
        return super().create(vlist)

    @classmethod
    def copy(cls, tanks, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('location', None)
        return super().copy(tanks, default=default)


class Pump(DeactivableMixin, ModelSQL, ModelView):
    "Fuel Pump"
    __name__ = 'fuel.pump'

    station = fields.Many2One(
        'fuel.station', "Station", required=True, ondelete='CASCADE',
        help="Station concerned.")
    code = fields.Char("Code", required=True,
        help="Short unique identifier.")
    name = fields.Char("Name",
        help="Name displayed in lists and searches.")
    nozzles = fields.One2Many('fuel.nozzle', 'pump', "Nozzles",
        help="Nozzles of the pump.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_station_unique', Unique(t, t.station, t.code),
                'fuel_station.msg_pump_code_unique'),
            ]
        cls._order.insert(0, ('station', 'ASC'))
        cls._order.insert(1, ('code', 'ASC'))

    def get_rec_name(self, name):
        return "%s / %s" % (self.station.code, self.name or self.code)


class Nozzle(DeactivableMixin, ModelSQL, ModelView):
    "Fuel Nozzle"
    __name__ = 'fuel.nozzle'

    pump = fields.Many2One(
        'fuel.pump', "Pump", required=True, ondelete='CASCADE',
        help="Pump concerned.")
    station = fields.Function(
        fields.Many2One('fuel.station', "Station",
        help="Station concerned."),
        'on_change_with_station', searcher='search_station')
    number = fields.Integer(
        "Number", required=True, domain=[('number', '>', 0)],
        help="Number assigned automatically by the sequence.")
    tank = fields.Many2One(
        'fuel.tank', "Tank", required=True,
        domain=[('station', '=', Eval('station', -1))],
        help="Tank concerned.")
    product = fields.Function(
        fields.Many2One('product.product', "Fuel",
        help="Product concerned."),
        'on_change_with_product', searcher='search_product')
    meter_index = fields.Float(
        "Meter Index", digits=(16, 3), required=True,
        domain=[('meter_index', '>=', 0)],
        help="Current reading of the totalizer (gallons). It is updated "
        "automatically when a shift is closed.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('number_pump_unique', Unique(t, t.pump, t.number),
                'fuel_station.msg_nozzle_number_unique'),
            ]
        cls._order.insert(0, ('pump', 'ASC'))
        cls._order.insert(1, ('number', 'ASC'))

    @classmethod
    def default_meter_index(cls):
        return 0.

    @fields.depends('pump', '_parent_pump.station')
    def on_change_with_station(self, name=None):
        if self.pump:
            return self.pump.station.id

    @classmethod
    def search_station(cls, name, clause):
        return [('pump.station',) + tuple(clause[1:])]

    @fields.depends('tank')
    def on_change_with_product(self, name=None):
        if self.tank:
            return self.tank.product.id

    @classmethod
    def search_product(cls, name, clause):
        return [('tank.product',) + tuple(clause[1:])]

    def get_rec_name(self, name):
        text = "%s-%s" % (self.pump.code, self.number)
        if self.tank:
            text += " (%s)" % self.tank.product.rec_name
        return text

    @classmethod
    def validate(cls, nozzles):
        super().validate(nozzles)
        for nozzle in nozzles:
            if nozzle.tank.station != nozzle.pump.station:
                raise FuelError(gettext(
                        'fuel_station.msg_nozzle_station',
                        nozzle=nozzle.rec_name))
