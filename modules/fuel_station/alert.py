# This file is part of the Tryton fuel_station module.
"""Scheduled alerts: low tanks and shifts open for too long."""
import datetime
import logging

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import without_check_access

logger = logging.getLogger(__name__)


def _users(xml_id):
    pool = Pool()
    UserGroup = pool.get('res.user-res.group')
    ModelData = pool.get('ir.model.data')
    group = ModelData.get_id('fuel_station', xml_id)
    return {ug.user.id for ug in UserGroup.search([('group', '=', group)])}


def _push(users, title, body, priority):
    from trytond import bus
    for user in users:
        try:
            bus.notify(title, body, priority, user=user)
        except Exception:  # never block the scheduler on a pop-up
            logger.warning("Cannot push notification", exc_info=True)


class Station(metaclass=PoolMeta):
    __name__ = 'fuel.station'

    shift_max_hours = fields.Integer(
        "Maximum Shift Length (h)", required=True,
        domain=[('shift_max_hours', '>=', 1)],
        help="A shift still open after this number of hours raises a "
        "notification to the station managers.")

    @classmethod
    def default_shift_max_hours(cls):
        return 12

    @classmethod
    def notify_alerts(cls):
        """Entry point of the scheduled action: low tanks, long shifts.

        Return the number of alerts pushed."""
        pool = Pool()
        return (pool.get('fuel.tank').notify_low_levels()
            + pool.get('fuel.shift').notify_long_open())


class Tank(metaclass=PoolMeta):
    __name__ = 'fuel.tank'

    low_notified = fields.Boolean(
        "Low Level Notified", readonly=True,
        help="A notification was sent for the current low level. It is "
        "reset when the tank is refilled above its minimum level.")

    @classmethod
    def default_low_notified(cls):
        return False

    @classmethod
    def copy(cls, tanks, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('low_notified', False)
        return super().copy(tanks, default=default)

    @classmethod
    def notify_low_levels(cls):
        tanks = cls.search([])
        quantities = cls.quantities(tanks)
        newly, refilled = [], []
        for tank in tanks:
            low = quantities.get(tank.id, 0.) < tank.min_level
            if low and not tank.low_notified:
                newly.append(tank)
            elif not low and tank.low_notified:
                refilled.append(tank)
        if newly:
            body = '\n'.join(gettext(
                    'fuel_station.msg_alert_tank_item',
                    tank=t.rec_name, quantity=round(quantities[t.id], 1),
                    minimum=t.min_level) for t in newly[:10])
            _push(_users('group_fuel'), gettext(
                    'fuel_station.msg_alert_tank_title', count=len(newly)),
                body, 4)
        with without_check_access():
            if newly:
                cls.write(newly, {'low_notified': True})
            if refilled:
                cls.write(refilled, {'low_notified': False})
        return len(newly)


class Shift(metaclass=PoolMeta):
    __name__ = 'fuel.shift'

    long_notified = fields.Boolean(
        "Long Shift Notified", readonly=True,
        help="A notification was sent because the shift is open for too "
        "long.")

    @classmethod
    def default_long_notified(cls):
        return False

    @classmethod
    def copy(cls, shifts, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('long_notified', False)
        return super().copy(shifts, default=default)

    @classmethod
    def notify_long_open(cls):
        now = datetime.datetime.now()
        late = []
        for shift in cls.search([
                    ('state', '=', 'open'), ('long_notified', '=', False)]):
            opened = shift.create_date or now
            if now - opened >= datetime.timedelta(
                    hours=shift.station.shift_max_hours):
                late.append(shift)
        if late:
            body = '\n'.join(gettext(
                    'fuel_station.msg_alert_shift_item',
                    shift=s.rec_name, attendant=s.attendant.rec_name)
                for s in late[:10])
            _push(_users('group_fuel_admin'), gettext(
                    'fuel_station.msg_alert_shift_title', count=len(late)),
                body, 2)
            with without_check_access():
                cls.write(late, {'long_notified': True})
        return len(late)


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('fuel.station|notify_alerts', "Notify Fuel Station Alerts"))
