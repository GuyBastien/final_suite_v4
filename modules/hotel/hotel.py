# This file is part of the Tryton hotel module.
import datetime

from trytond.model import DeactivableMixin, ModelSQL, ModelView, Unique, fields
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction


class Hotel(DeactivableMixin, ModelSQL, ModelView):
    "Hotel"
    __name__ = 'hotel.hotel'

    name = fields.Char("Name", required=True, translate=True)
    code = fields.Char("Code", required=True)
    company = fields.Many2One('company.company', "Company", required=True)
    currency = fields.Function(
        fields.Many2One('currency.currency', "Currency"),
        'on_change_with_currency')
    party = fields.Many2One(
        'party.party', "Party",
        context={'company': Eval('company', -1)},
        help="The party representing the hotel (address, contacts).")
    warehouse = fields.Many2One(
        'stock.location', "Warehouse", required=True,
        domain=[('type', '=', 'warehouse')],
        help="The warehouse storing the hotel articles and consumables.")
    consumption_location = fields.Many2One(
        'stock.location', "Consumption Location", required=True,
        domain=[('type', 'in', ['customer', 'lost_found'])],
        help="Where the stock goes when consumed in a room.")
    check_in_time = fields.Time("Check-in Time", required=True)
    check_out_time = fields.Time("Check-out Time", required=True)
    room_types = fields.One2Many('hotel.room.type', 'hotel', "Room Types")
    rooms = fields.One2Many('hotel.room', 'hotel', "Rooms")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_company_unique', Unique(t, t.company, t.code),
                'hotel.msg_hotel_code_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_warehouse(cls):
        return Pool().get('stock.location').get_default_warehouse()

    @classmethod
    def default_consumption_location(cls):
        Location = Pool().get('stock.location')
        locations = Location.search([('type', '=', 'customer')], limit=1)
        if locations:
            return locations[0].id

    @classmethod
    def default_check_in_time(cls):
        return datetime.time(14, 0)

    @classmethod
    def default_check_out_time(cls):
        return datetime.time(11, 0)

    @fields.depends('company')
    def on_change_with_currency(self, name=None):
        if self.company:
            return self.company.currency.id
