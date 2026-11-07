# This file is part of the Tryton hotel_restaurant module.
"""Link of the restaurant with the hotel.

* a restaurant belongs to a hotel;
* an order can be charged to the room of a checked-in guest: the amount
  becomes a charge of the reservation, billed with the stay (hotel_pms);
* a table booking can be made for a guest of the hotel.

The tables and the table bookings of the restaurant are the only ones: those
that hotel_services kept on its own are imported once, then hidden."""
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.modules.product import round_price
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If

from .exceptions import HotelRestaurantError

_ZERO = Decimal(0)


class Restaurant(metaclass=PoolMeta):
    __name__ = 'restaurant.restaurant'

    hotel = fields.Many2One(
        'hotel.hotel', "Hotel",
        domain=[('company', '=', Eval('company', -1))],
        help="Hotel to which this restaurant belongs. The guests of this "
        "hotel can charge their orders to their room.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'import_hotel_bookings': {
                    'invisible': ~Eval('hotel'),
                    'depends': ['hotel']},
                })

    @classmethod
    @ModelView.button
    def import_hotel_bookings(cls, restaurants):
        """Bring the tables and the open table bookings that the hotel kept
        on its own (hotel_services) into this restaurant. Run it once."""
        pool = Pool()
        HotelTable = pool.get('hotel.restaurant.table')
        HotelBooking = pool.get('hotel.restaurant.booking')
        Area = pool.get('restaurant.area')
        Table = pool.get('restaurant.table')
        Reservation = pool.get('restaurant.reservation')
        for restaurant in restaurants:
            if not restaurant.hotel:
                raise HotelRestaurantError(gettext(
                        'hotel_restaurant.msg_no_hotel',
                        restaurant=restaurant.rec_name))
            tables = {t.code: t for t in restaurant.tables}
            area = restaurant.areas[0] if restaurant.areas else None
            for old in HotelTable.search([
                        ('hotel', '=', restaurant.hotel.id),
                        ('restaurant_table', '=', None)]):
                table = tables.get(old.name)
                if table is None:
                    if area is None:
                        area, = Area.create([{
                                    'restaurant': restaurant.id,
                                    'name': old.area or gettext(
                                        'hotel_restaurant.msg_area')}])
                    table, = Table.create([{
                                'restaurant': restaurant.id,
                                'area': area.id, 'code': old.name,
                                'seats': old.seats}])
                    tables[old.name] = table
                HotelTable.write([old], {'restaurant_table': table.id})
            for old in HotelBooking.search([
                        ('hotel', '=', restaurant.hotel.id),
                        ('state', '=', 'booked'),
                        ('restaurant_reservation', '=', None)]):
                if old.table.restaurant_table is None:
                    continue
                new, = Reservation.create([{
                            'company': restaurant.company.id,
                            'restaurant': restaurant.id,
                            'party': old.party.id if old.party else None,
                            'guest_name': old.guest_name or (
                                old.party.rec_name if old.party else None),
                            'date': old.date, 'time': old.time,
                            'duration': old.duration, 'persons': old.covers,
                            'table': old.table.restaurant_table.id,
                            'hotel_reservation': (
                                old.reservation.id if old.reservation
                                else None),
                            'notes': old.notes}])
                HotelBooking.write([old], {'restaurant_reservation': new.id})


class Order(metaclass=PoolMeta):
    __name__ = 'restaurant.order'

    hotel = fields.Function(
        fields.Many2One('hotel.hotel', "Hotel"), 'on_change_with_hotel')
    reservation = fields.Many2One(
        'hotel.reservation', "Hotel Stay",
        domain=[
            ('state', '=', 'checked_in'),
            ('company', '=', Eval('company', -1)),
            If(Bool(Eval('hotel')), ('hotel', '=', Eval('hotel', -1)), ()),
            ],
        states={'readonly': Eval('state') != 'open'},
        help="Checked-in stay of the guest. Needed to charge the order to "
        "the room.")

    @fields.depends('restaurant')
    def on_change_with_hotel(self, name=None):
        if self.restaurant and self.restaurant.hotel:
            return self.restaurant.hotel.id

    @fields.depends('reservation', 'party')
    def on_change_reservation(self):
        if self.reservation and not self.party:
            self.party = self.reservation.party

    @classmethod
    def copy(cls, orders, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('reservation', None)
        return super().copy(orders, default=default)

    @classmethod
    def validate(cls, orders):
        super().validate(orders)
        for order in orders:
            hotel = order.restaurant.hotel
            if (order.reservation and hotel
                    and order.reservation.hotel != hotel):
                raise HotelRestaurantError(gettext(
                        'hotel_restaurant.msg_other_hotel',
                        order=order.rec_name, hotel=hotel.rec_name))


class Payment(metaclass=PoolMeta):
    __name__ = 'restaurant.payment'

    hotel_charge = fields.Many2One(
        'hotel.reservation.charge', "Room Charge", readonly=True,
        ondelete='RESTRICT',
        states={'invisible': ~Eval('hotel_charge')},
        help="Charge of the hotel stay created by this payment.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection = cls.method.selection + [
            ('room', "Room Charge")]

    @classmethod
    def validate(cls, payments):
        super().validate(payments)
        for payment in payments:
            if payment.method != 'room':
                continue
            stay = payment.order.reservation
            if stay is None or stay.state != 'checked_in':
                raise HotelRestaurantError(gettext(
                        'hotel_restaurant.msg_room_no_stay',
                        order=payment.order.rec_name))

    @classmethod
    def create(cls, vlist):
        payments = super().create(vlist)
        cls._create_charges([p for p in payments if p.method == 'room'])
        return payments

    @classmethod
    def _create_charges(cls, payments):
        Charge = Pool().get('hotel.reservation.charge')
        for payment in payments:
            stay = payment.order.reservation
            product = stay.hotel.room_charge_product
            if product is None:
                raise HotelRestaurantError(gettext(
                        'hotel_restaurant.msg_no_room_product',
                        hotel=stay.hotel.rec_name))
            charge, = Charge.create([{
                        'reservation': stay.id,
                        'product': product.id,
                        'description': gettext(
                            'hotel_restaurant.msg_charge_description',
                            order=payment.order.rec_name),
                        'quantity': 1.,
                        'unit': product.sale_uom.id,
                        'unit_price': round_price(payment.amount)}])
            cls.write([payment], {'hotel_charge': charge.id})

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for payments, values in zip(actions, actions):
            for payment in payments:
                if payment.hotel_charge and set(values) - {'hotel_charge'}:
                    raise HotelRestaurantError(gettext(
                            'hotel_restaurant.msg_room_locked',
                            order=payment.order.rec_name))
        super().write(*args)

    @classmethod
    def delete(cls, payments):
        Charge = Pool().get('hotel.reservation.charge')
        charges = []
        for payment in payments:
            if payment.hotel_charge:
                if payment.hotel_charge.reservation.sale:
                    raise HotelRestaurantError(gettext(
                            'hotel_restaurant.msg_charge_billed',
                            order=payment.order.rec_name))
                charges.append(payment.hotel_charge)
        super().delete(payments)
        if charges:
            Charge.delete(charges)


class TableReservation(metaclass=PoolMeta):
    __name__ = 'restaurant.reservation'

    hotel_reservation = fields.Many2One(
        'hotel.reservation', "Hotel Stay",
        domain=[
            ('state', 'in', ['confirmed', 'checked_in']),
            ('company', '=', Eval('company', -1))],
        states={'readonly': Eval('state') != 'booked'},
        help="Stay of the hotel guest who books the table. The order of "
        "the table is then linked to the stay.")

    @fields.depends('hotel_reservation', 'party', 'guest_name')
    def on_change_hotel_reservation(self):
        if self.hotel_reservation and not self.party:
            self.party = self.hotel_reservation.party
            self.guest_name = self.hotel_reservation.party.rec_name

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('hotel_reservation', None)
        return super().copy(reservations, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('seated')
    def seat(cls, reservations):
        super().seat(reservations)
        Order = Pool().get('restaurant.order')
        for r in reservations:
            r = cls(r.id)
            if (r.order and r.hotel_reservation
                    and r.hotel_reservation.state == 'checked_in'):
                Order.write([r.order], {
                        'reservation': r.hotel_reservation.id})


class HotelReservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    restaurant_orders = fields.Function(fields.One2Many(
            'restaurant.order', None, "Restaurant Orders",
            help="Orders of the restaurant of the hotel for this stay."),
        'get_restaurant_orders')

    @classmethod
    def get_restaurant_orders(cls, reservations, name):
        Order = Pool().get('restaurant.order')
        result = {r.id: [] for r in reservations}
        for order in Order.search([
                    ('reservation', 'in', [r.id for r in reservations])]):
            result[order.reservation.id].append(order.id)
        return result

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_out')
    def check_out(cls, reservations):
        # An order with a balance due cannot be charged to the room after the
        # departure: make the receptionist settle it first.
        Order = Pool().get('restaurant.order')
        open_orders = [o for o in Order.search([
                    ('reservation', 'in', [r.id for r in reservations]),
                    ('state', '=', 'open')]) if o.balance > 0]
        if open_orders:
            order = open_orders[0]
            raise HotelRestaurantError(gettext(
                    'hotel_restaurant.msg_open_order_checkout',
                    order=order.rec_name,
                    reservation=order.reservation.rec_name))
        super().check_out(reservations)


class HotelTable(metaclass=PoolMeta):
    __name__ = 'hotel.restaurant.table'

    restaurant_table = fields.Many2One(
        'restaurant.table', "Restaurant Table", readonly=True,
        help="Table of the restaurant that replaces this one.")


class HotelTableBooking(metaclass=PoolMeta):
    __name__ = 'hotel.restaurant.booking'

    restaurant_reservation = fields.Many2One(
        'restaurant.reservation', "Restaurant Reservation", readonly=True,
        help="Reservation of the restaurant that replaces this one.")
