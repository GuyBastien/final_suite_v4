# This file is part of the Tryton fuel_station module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.modules.product import price_digits
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction, without_check_access

from .exceptions import DeliveryError, FuelWarning
from .tools import volume_correction_factor


class Delivery(Workflow, ModelSQL, ModelView):
    "Fuel Delivery"
    __name__ = 'fuel.delivery'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True,
        help="Number assigned automatically by the sequence.")
    company = fields.Many2One(
        'company.company', "Company", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="Company that owns the record.")
    station = fields.Many2One(
        'fuel.station', "Station", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states={'readonly': (Eval('state') != 'draft')
            | Bool(Eval('lines', []))},
        help="Station concerned.")
    supplier = fields.Many2One(
        'party.party', "Supplier", required=True,
        context={'company': Eval('company', -1)},
        states={'readonly': Eval('state') != 'draft'},
        help="Company that supplies the goods.")
    date = fields.Date(
        "Date", required=True,
        states={'readonly': Eval('state') != 'draft'},
        help="Date of the document.")
    reference = fields.Char(
        "Delivery Note", states={'readonly': Eval('state') != 'draft'},
        help="Number of the supplier's delivery note.")
    driver = fields.Char(
        "Driver / Truck", states={'readonly': Eval('state') != 'draft'},
        help="Person in charge of the delivery.")
    lines = fields.One2Many(
        'fuel.delivery.line', 'delivery', "Lines",
        states={'readonly': Eval('state') != 'draft'},
        help="Detail lines of the document.")
    total_quantity = fields.Function(
        fields.Float("Total Quantity", digits=(16, 3),
        help="Total volume."), 'get_total')
    notes = fields.Text("Notes",
        help="Free text for internal use.")
    state = fields.Selection([
            ('draft', "Draft"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True,
        help="Current step of the workflow.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._transitions |= {
            ('draft', 'done'),
            ('draft', 'cancelled'),
            ('cancelled', 'draft'),
            }
        cls._buttons.update({
                'cancel': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state']},
                'draft': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state']},
                'receive': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state']},
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(
                    Eval('state') == 'cancelled', 'muted',
                    If(Eval('state') == 'open', 'warning',
                        If(Eval('state') == 'done', 'success', '')))),
            ]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'draft'

    def get_total(self, name):
        return sum(l.quantity or 0. for l in self.lines)

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        vlist = [v.copy() for v in vlist]
        sequence = Sequence(
            ModelData.get_id('fuel_station', 'sequence_delivery'))
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, deliveries, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        return super().copy(deliveries, default=default)

    @classmethod
    def delete(cls, deliveries):
        for delivery in deliveries:
            if delivery.state == 'done':
                raise DeliveryError(gettext(
                        'fuel_station.msg_delivery_delete_done',
                        delivery=delivery.rec_name))
        super().delete(deliveries)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, deliveries):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, deliveries):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def receive(cls, deliveries):
        pool = Pool()
        Move = pool.get('stock.move')
        Line = pool.get('fuel.delivery.line')
        Tank = pool.get('fuel.tank')
        Warning = pool.get('res.user.warning')
        moves, lines = [], []
        for delivery in deliveries:
            if not delivery.lines:
                raise DeliveryError(gettext(
                        'fuel_station.msg_delivery_no_lines',
                        delivery=delivery.rec_name))
            added = {}
            for line in delivery.lines:
                added[line.tank] = added.get(line.tank, 0.) + line.quantity
            quantities = Tank.quantities(list(added))
            for tank, quantity in added.items():
                free = tank.capacity - quantities.get(tank.id, 0.)
                if quantity > free + 1e-6:
                    key = 'fuel_delivery_over_%s_%s' % (delivery.id, tank.id)
                    if Warning.check(key):
                        raise FuelWarning(key, gettext(
                                'fuel_station.msg_delivery_overfill',
                                tank=tank.rec_name, quantity=quantity,
                                free=max(free, 0.)))
            for line in delivery.lines:
                line.move = line.get_move()
                moves.append(line.move)
                lines.append(line)
        with without_check_access():
            Move.save(moves)
            Line.save(lines)
            Move.do(moves)


class DeliveryLine(ModelSQL, ModelView):
    "Fuel Delivery Line"
    __name__ = 'fuel.delivery.line'

    delivery = fields.Many2One(
        'fuel.delivery', "Delivery", required=True, ondelete='CASCADE',
        help="Fuel delivery linked to this record.")
    tank = fields.Many2One(
        'fuel.tank', "Tank", required=True,
        states={'readonly': Bool(Eval('move'))},
        help="Tank concerned.")
    product = fields.Function(
        fields.Many2One('product.product', "Fuel",
        help="Product concerned."),
        'on_change_with_product')
    unit = fields.Function(
        fields.Many2One('product.uom', "Unit",
        help="Unit of measure."), 'on_change_with_unit')
    ordered_quantity = fields.Float(
        "Delivery Note Quantity", digits='unit',
        domain=[('ordered_quantity', '>=', 0)],
        help="Quantity written on the supplier's delivery note.")
    quantity = fields.Float(
        "Received Quantity", digits='unit', required=True,
        domain=[('quantity', '>', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Quantity really measured in the tank after unloading.")
    temperature = fields.Float(
        "Temperature (°C)", digits=(16, 1),
        states={'readonly': Bool(Eval('move'))},
        help="Temperature of the fuel measured at unloading.")
    density = fields.Float(
        "Density", digits=(16, 1),
        domain=[('density', '>=', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Density at 15 °C in kg/m³. Defaults to the density of the "
        "fuel.")
    vcf = fields.Function(
        fields.Float("Correction Factor", digits=(16, 5),
        help="Factor converting the measured volume to 15 °C."),
        'on_change_with_vcf')
    net_quantity = fields.Function(
        fields.Float("Quantity at 15 °C", digits='unit',
        help="Volume converted to the reference temperature of 15 °C."),
        'on_change_with_net_quantity')
    unit_cost = fields.Numeric(
        "Unit Cost", digits=price_digits, required=True,
        domain=[('unit_cost', '>=', 0)],
        states={'readonly': Bool(Eval('move'))},
        help="Purchase cost per gallon (without tax).")
    move = fields.Many2One('stock.move', "Stock Move", readonly=True,
        help="Stock move generated by this record.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('delivery', 'ASC'))
        cls._order.insert(1, ('id', 'ASC'))

    @classmethod
    def default_unit_cost(cls):
        return Decimal(0)

    @fields.depends('tank')
    def on_change_with_product(self, name=None):
        if self.tank:
            return self.tank.product.id

    @fields.depends('tank')
    def on_change_with_unit(self, name=None):
        if self.tank:
            return self.tank.product.default_uom.id

    @fields.depends('tank', 'unit_cost', 'density')
    def on_change_tank(self):
        if self.tank:
            if not self.unit_cost:
                self.unit_cost = self.tank.product.cost_price
            if not self.density:
                self.density = self.tank.product.template.fuel_density

    @fields.depends('tank', 'temperature', 'density')
    def on_change_with_vcf(self, name=None):
        if not self.tank:
            return 1.0
        template = self.tank.product.template
        return volume_correction_factor(
            template.fuel_family,
            self.density or template.fuel_density, self.temperature)

    @fields.depends('quantity', 'tank', 'temperature', 'density')
    def on_change_with_net_quantity(self, name=None):
        return round((self.quantity or 0.) * self.on_change_with_vcf(), 3)

    def booked_quantity(self):
        "Quantity written in the stock"
        if self.delivery.station.temperature_correction:
            return self.on_change_with_net_quantity()
        return self.quantity

    @fields.depends('ordered_quantity', 'quantity')
    def on_change_ordered_quantity(self):
        if self.ordered_quantity and not self.quantity:
            self.quantity = self.ordered_quantity

    @classmethod
    def validate(cls, lines):
        super().validate(lines)
        for line in lines:
            if line.tank.station != line.delivery.station:
                raise DeliveryError(gettext(
                        'fuel_station.msg_delivery_tank_station',
                        tank=line.tank.rec_name,
                        station=line.delivery.station.rec_name))

    @classmethod
    def copy(cls, lines, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('move', None)
        return super().copy(lines, default=default)

    def get_move(self):
        Move = Pool().get('stock.move')
        delivery = self.delivery
        move = Move()
        move.company = delivery.company
        move.currency = delivery.company.currency
        move.product = self.tank.product
        move.unit = self.tank.product.default_uom
        move.quantity = self.booked_quantity()
        move.from_location = delivery.supplier.supplier_location
        move.to_location = self.tank.location
        move.effective_date = delivery.date
        move.unit_price = self.unit_cost
        move.origin = self
        return move
