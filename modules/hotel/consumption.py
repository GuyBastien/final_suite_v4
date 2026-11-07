# This file is part of the Tryton hotel module.
from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Workflow, fields
from trytond.modules.product import price_digits
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If
from trytond.transaction import Transaction

from .exceptions import ConsumptionError

_STATES = {'readonly': Eval('state') != 'draft'}


class Consumption(Workflow, ModelSQL, ModelView):
    "Hotel Stock Operation"
    __name__ = 'hotel.consumption'
    _rec_name = 'number'

    number = fields.Char("Number", readonly=True)
    company = fields.Many2One(
        'company.company', "Company", required=True, states=_STATES)
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True,
        domain=[('company', '=', Eval('company', -1))],
        states=_STATES)
    kind = fields.Selection([
            ('consumption', "Consumption"),
            ('replenishment', "Replenishment"),
            ], "Kind", required=True, states=_STATES,
        help="Consumption takes the articles out of the room (or the "
        "warehouse) to the consumption location. Replenishment moves them "
        "from the warehouse into the room.")
    room = fields.Many2One(
        'hotel.room', "Room",
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={
            'readonly': Eval('state') != 'draft',
            'required': Eval('kind') == 'replenishment',
            })
    date = fields.Date("Date", required=True, states=_STATES)
    lines = fields.One2Many(
        'hotel.consumption.line', 'consumption', "Lines", states=_STATES)
    notes = fields.Text("Notes", states=_STATES)
    state = fields.Selection([
            ('draft', "Draft"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'cancelled', 'muted',
                        ''))),
            ]

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
                    'depends': ['state'],
                    },
                'draft': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                'process': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_kind(cls):
        return 'consumption'

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_state(cls):
        return 'draft'

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        ModelData = pool.get('ir.model.data')
        sequence = Sequence(ModelData.get_id('hotel', 'sequence_consumption'))
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('number'):
                values['number'] = sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, consumptions, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('number', None)
        return super().copy(consumptions, default=default)

    @classmethod
    def delete(cls, consumptions):
        for consumption in consumptions:
            if consumption.state == 'done':
                raise ConsumptionError(gettext(
                        'hotel.msg_consumption_delete_done',
                        consumption=consumption.rec_name))
        super().delete(consumptions)

    def _move_locations(self):
        "Return the (from, to) locations of the moves"
        warehouse_storage = self.hotel.warehouse.storage_location
        room_location = self.room.location if self.room else None
        if self.kind == 'replenishment':
            if room_location is None:
                raise ConsumptionError(gettext(
                        'hotel.msg_consumption_no_room_location',
                        consumption=self.rec_name))
            return warehouse_storage, room_location
        return (room_location or warehouse_storage,
            self.hotel.consumption_location)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, consumptions):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, consumptions):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def process(cls, consumptions):
        pool = Pool()
        Move = pool.get('stock.move')
        Line = pool.get('hotel.consumption.line')
        lines, moves = [], []
        for consumption in consumptions:
            if not consumption.lines:
                raise ConsumptionError(gettext(
                        'hotel.msg_consumption_no_lines',
                        consumption=consumption.rec_name))
            from_location, to_location = consumption._move_locations()
            for line in consumption.lines:
                line.move = line.get_move(from_location, to_location)
                lines.append(line)
                moves.append(line.move)
        Move.save(moves)
        Line.save(lines)
        Move.do(moves)


class ConsumptionLine(ModelSQL, ModelView):
    "Hotel Stock Operation Line"
    __name__ = 'hotel.consumption.line'

    consumption = fields.Many2One(
        'hotel.consumption', "Operation", required=True, ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[
            ('template.hotel_article', '=', True),
            ('type', '=', 'goods'),
            ],
        states={'readonly': Bool(Eval('move', -1))})
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', "Product UoM Category"),
        'on_change_with_product_uom_category')
    quantity = fields.Float(
        "Quantity", digits='unit', required=True,
        domain=[('quantity', '>', 0)])
    unit = fields.Many2One(
        'product.uom', "Unit", required=True,
        domain=[('category', '=', Eval('product_uom_category', -1))])
    billable = fields.Boolean(
        "Billable",
        help="Charge this consumption to the guest.")
    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits,
        states={'required': Bool(Eval('billable'))},
        help="The price charged to the guest when billable.")
    move = fields.Many2One('stock.move', "Stock Move", readonly=True)

    @classmethod
    def default_quantity(cls):
        return 1.

    @classmethod
    def default_billable(cls):
        return False

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('product', 'unit', 'unit_price', 'billable')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.default_uom
            self.billable = self.product.template.hotel_billable
            if self.billable:
                self.unit_price = self.product.list_price

    @classmethod
    def copy(cls, lines, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('move', None)
        return super().copy(lines, default=default)

    def get_move(self, from_location, to_location):
        pool = Pool()
        Move = pool.get('stock.move')
        consumption = self.consumption
        move = Move()
        move.company = consumption.company
        move.currency = consumption.company.currency
        move.product = self.product
        move.unit = self.unit
        move.quantity = self.quantity
        move.from_location = from_location
        move.to_location = to_location
        move.effective_date = consumption.date
        move.origin = self
        # The stock module requires a price on moves to a customer location
        if self.unit_price is not None:
            move.unit_price = self.unit_price
        elif move.on_change_with_unit_price_required():
            move.unit_price = self.product.cost_price
        return move


class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'

    @classmethod
    def _get_origin(cls):
        return super()._get_origin() + ['hotel.consumption.line']
