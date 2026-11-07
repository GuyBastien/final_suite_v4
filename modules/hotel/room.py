# This file is part of the Tryton hotel module.
import datetime

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields, sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Eval, If

from .exceptions import RateError

ROOM_STATES = [
    ('available', "Available"),
    ('occupied', "Occupied"),
    ('dirty', "Dirty"),
    ('maintenance', "Maintenance"),
    ('out_of_order', "Out of Order"),
    ]


class RoomType(DeactivableMixin, ModelSQL, ModelView):
    "Room Type"
    __name__ = 'hotel.room.type'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Name", required=True, translate=True)
    code = fields.Char("Code", required=True)
    capacity = fields.Integer(
        "Capacity", required=True, domain=[('capacity', '>', 0)])
    description = fields.Text("Description", translate=True)
    night_product = fields.Many2One(
        'product.product', "Night Product",
        domain=[('type', '=', 'service')],
        help="The service product invoiced for a night.")
    hour_product = fields.Many2One(
        'product.product', "Hour Product",
        domain=[('type', '=', 'service')],
        help="The service product invoiced for an hour.")
    rates = fields.One2Many('hotel.rate', 'room_type', "Rates")
    articles = fields.One2Many(
        'hotel.room.type.article', 'room_type', "Standard Articles",
        help="Articles and consumables normally stocked in each room.")
    rooms = fields.One2Many('hotel.room', 'type', "Rooms")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_hotel_unique', Unique(t, t.hotel, t.code),
                'hotel.msg_room_type_code_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_capacity(cls):
        return 2

    def get_rate(self, date):
        "Return the most specific active rate valid at date"
        candidates = [
            r for r in self.rates
            if (r.start_date is None or r.start_date <= date)
            and (r.end_date is None or date <= r.end_date)]
        if candidates:
            return max(candidates, key=lambda r: r.start_date or datetime.date.min)

    def compute_price(self, start, end, mode=None):
        "Compute the price of a stay between two datetimes"
        rate = self.get_rate(start.date())
        if rate is None:
            raise RateError(gettext(
                    'hotel.msg_room_type_no_rate',
                    room_type=self.rec_name, date=start.date()))
        return rate.compute(start, end, mode=mode)


class RoomTypeArticle(sequence_ordered(), ModelSQL, ModelView):
    "Room Type Standard Article"
    __name__ = 'hotel.room.type.article'

    room_type = fields.Many2One(
        'hotel.room.type', "Room Type", required=True, ondelete='CASCADE')
    product = fields.Many2One(
        'product.product', "Product", required=True,
        domain=[
            ('template.hotel_article', '=', True),
            ('type', '=', 'goods'),
            ])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', "Product UoM Category"),
        'on_change_with_product_uom_category')
    quantity = fields.Float("Quantity", digits='unit', required=True,
        domain=[('quantity', '>', 0)])
    unit = fields.Many2One(
        'product.uom', "Unit", required=True,
        domain=[('category', '=', Eval('product_uom_category', -1))])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('product_unique', Unique(t, t.room_type, t.product),
                'hotel.msg_room_type_article_unique'),
            ]

    @classmethod
    def default_quantity(cls):
        return 1.

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('product', 'unit')
    def on_change_product(self):
        if self.product:
            self.unit = self.product.default_uom


class Room(DeactivableMixin, ModelSQL, ModelView):
    "Hotel Room"
    __name__ = 'hotel.room'

    hotel = fields.Many2One('hotel.hotel', "Hotel", required=True)
    name = fields.Char("Number", required=True)
    type = fields.Many2One(
        'hotel.room.type', "Type", required=True,
        domain=[('hotel', '=', Eval('hotel', -1))])
    floor = fields.Integer("Floor")
    state = fields.Selection(ROOM_STATES, "State", required=True, readonly=True)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'available', 'success',
                        If(Eval('state') == 'dirty', 'warning',
                        If(Eval('state') == 'maintenance', 'danger',
                        If(Eval('state') == 'out_of_order', 'danger',
                        ''))))),
            ]
    location = fields.Many2One(
        'stock.location', "Stock Location",
        domain=[('type', '=', 'storage')],
        help="The stock location of the room (minibar, linen, amenities). "
        "Created automatically if left empty.")
    notes = fields.Text("Notes")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('name_hotel_unique', Unique(t, t.hotel, t.name),
                'hotel.msg_room_name_unique'),
            ]
        cls._order.insert(0, ('hotel', 'ASC'))
        cls._order.insert(1, ('name', 'ASC'))
        cls._buttons.update({
                'make_available': {
                    'invisible': Eval('state').in_(['available', 'occupied']),
                    'depends': ['state'],
                    },
                'make_dirty': {
                    'invisible': Eval('state') != 'occupied',
                    'depends': ['state'],
                    },
                'make_maintenance': {
                    'invisible': Eval('state').in_(
                        ['occupied', 'maintenance']),
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'available'

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Location = pool.get('stock.location')
        Hotel = pool.get('hotel.hotel')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('location') and values.get('hotel'):
                hotel = Hotel(values['hotel'])
                location, = Location.create([{
                            'name': "%s/%s" % (hotel.code, values['name']),
                            'type': 'storage',
                            'parent': hotel.warehouse.storage_location.id,
                            }])
                values['location'] = location.id
        return super().create(vlist)

    @classmethod
    @ModelView.button
    def make_available(cls, rooms):
        cls.write(rooms, {'state': 'available'})

    @classmethod
    @ModelView.button
    def make_dirty(cls, rooms):
        cls.write(rooms, {'state': 'dirty'})

    @classmethod
    @ModelView.button
    def make_maintenance(cls, rooms):
        cls.write(rooms, {'state': 'maintenance'})
