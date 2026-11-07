# This file is part of the Tryton hotel_pms module.
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval, If


class Consumption(metaclass=PoolMeta):
    __name__ = 'hotel.consumption'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation",
        domain=[
            ('hotel', '=', Eval('hotel', -1)),
            If(Bool(Eval('room')), ('lines.room', '=', Eval('room', -1)), ()),
            ],
        states={
            'readonly': Eval('state') != 'draft',
            'invisible': Eval('kind') != 'consumption',
            },
        help="The stay to which billable consumptions are charged at "
        "check-out.")

    @classmethod
    def _checked_in_reservation(cls, room_id):
        Reservation = Pool().get('hotel.reservation')
        reservations = Reservation.search([
                ('lines.room', '=', room_id),
                ('state', '=', 'checked_in'),
                ], limit=1)
        return reservations[0] if reservations else None

    @fields.depends('room', 'kind', 'reservation')
    def on_change_room(self):
        self.reservation = None
        if self.room and self.kind == 'consumption':
            self.reservation = self._checked_in_reservation(self.room.id)

    @classmethod
    def create(cls, vlist):
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if (values.get('room')
                    and values.get('kind', 'consumption') == 'consumption'
                    and not values.get('reservation')):
                reservation = cls._checked_in_reservation(values['room'])
                if reservation:
                    values['reservation'] = reservation.id
        return super().create(vlist)

    @classmethod
    def copy(cls, consumptions, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('reservation', None)
        return super().copy(consumptions, default=default)
