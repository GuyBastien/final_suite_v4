# This file is part of the Tryton hotel_pms module.
from trytond.pool import Pool, PoolMeta


class RoomType(metaclass=PoolMeta):
    __name__ = 'hotel.room.type'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        # Room products are sold through sale.sale
        for name in ['night_product', 'hour_product']:
            field = getattr(cls, name)
            field.domain = field.domain + [('salable', '=', True)]

    def get_available_rooms(self, start, end, exclude=None):
        "Return the rooms of the type free between start and end"
        return [r for r in self.rooms if r.is_available(start, end, exclude)]


class Room(metaclass=PoolMeta):
    __name__ = 'hotel.room'

    def is_available(self, start, end, exclude=None):
        """Return True if the room can be booked between start and end.

        Rooms in maintenance or out of order are never available. Only
        confirmed and checked-in reservations block a room; exclude is a
        reservation to ignore (the one being modified).
        """
        if not self.active or self.state in {'maintenance', 'out_of_order'}:
            return False
        Line = Pool().get('hotel.reservation.line')
        domain = [
            ('room', '=', self.id),
            ('reservation.state', 'in', ['confirmed', 'checked_in']),
            ('reservation.start', '<', end),
            ('reservation.end', '>', start),
            ]
        if exclude is not None:
            domain.append(('reservation', '!=', exclude.id))
        return not Line.search(domain, limit=1)
