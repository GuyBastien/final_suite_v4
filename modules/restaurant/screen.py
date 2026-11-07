# This file is part of the Tryton restaurant module.
"""Kitchen and bar screens: ideal times, delays and scheduled notifications."""
import datetime
import logging
from collections import defaultdict

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction, without_check_access

logger = logging.getLogger(__name__)

DELAYS = [
    ('', ""),
    ('ok', "On Time"),
    ('warning', "Over the Ideal Time"),
    ('late', "Very Late"),
    ]
LATE_RATIO = 1.5  # very late from 150 % of the ideal time
_PRIORITY = {1: 2, 2: 4}


class Restaurant(metaclass=PoolMeta):
    __name__ = 'restaurant.restaurant'

    kitchen_minutes = fields.Integer(
        "Kitchen Ideal Time (min)", required=True,
        domain=[('kitchen_minutes', '>=', 1)],
        help="Ideal time between sending an item to the kitchen and its "
        "readiness. Over it the line turns orange on the kitchen screen "
        "and the kitchen is notified; at 150% it turns red.")
    bar_minutes = fields.Integer(
        "Bar Ideal Time (min)", required=True,
        domain=[('bar_minutes', '>=', 1)],
        help="Ideal time between sending a drink to the bar and its "
        "readiness. The item can override it with its own preparation time.")
    serve_minutes = fields.Integer(
        "Ideal Serving Time (min)", required=True,
        domain=[('serve_minutes', '>=', 1)],
        help="Ideal time between an item ready and its service at the "
        "table. Over it the waiters are notified.")

    @classmethod
    def default_kitchen_minutes(cls):
        return 15

    @classmethod
    def default_bar_minutes(cls):
        return 5

    @classmethod
    def default_serve_minutes(cls):
        return 3


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    prep_minutes = fields.Integer(
        "Preparation Time (min)",
        domain=['OR', ('prep_minutes', '=', None),
            ('prep_minutes', '>=', 1)],
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Ideal preparation time of this item, in minutes. Empty uses "
        "the ideal time of the kitchen or of the bar of the restaurant.")


class OrderLine(metaclass=PoolMeta):
    __name__ = 'restaurant.order.line'

    ready_at = fields.DateTime(
        "Ready at", readonly=True,
        help="Time the item was ready to be served.")
    notified_level = fields.Integer(
        "Notified Level", readonly=True,
        help="Highest delay level already notified for the current step "
        "(0 none, 1 over the ideal time, 2 very late).")
    ideal_minutes = fields.Function(
        fields.Integer(
            "Ideal Time (min)",
            help="Ideal time of the current step: preparation, then "
            "service."),
        'get_delay')
    elapsed_minutes = fields.Function(
        fields.Integer(
            "Elapsed (min)",
            help="Minutes since the item was sent, or since it is ready."),
        'get_delay')
    delay = fields.Function(
        fields.Selection(
            DELAYS, "Delay",
            help="On time, over the ideal time, or very late (150% of the "
            "ideal time). Drives the colors of the kitchen and bar screens."),
        'get_delay')

    @classmethod
    def default_notified_level(cls):
        return 0

    @classmethod
    def copy(cls, lines, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('ready_at', None)
        default.setdefault('notified_level', 0)
        return super().copy(lines, default=default)

    def _delay_values(self, now):
        "(ideal minutes, elapsed minutes, delay) of the current step"
        if self.state in {'sent', 'preparing'} and self.sent_at:
            restaurant = self.order.restaurant
            ideal = self.product.template.prep_minutes
            if not ideal:
                ideal = (restaurant.bar_minutes
                    if self.station == 'bar' else restaurant.kitchen_minutes)
            start = self.sent_at
        elif self.state == 'ready' and self.ready_at:
            ideal = self.order.restaurant.serve_minutes
            start = self.ready_at
        else:
            return None, None, ''
        elapsed = int((now - start).total_seconds() // 60)
        seconds = (now - start).total_seconds()
        if seconds >= ideal * 60 * LATE_RATIO:
            delay = 'late'
        elif seconds >= ideal * 60:
            delay = 'warning'
        else:
            delay = 'ok'
        return ideal, elapsed, delay

    @classmethod
    def get_delay(cls, lines, names):
        now = datetime.datetime.now()
        result = {name: {} for name in names}
        for line in lines:
            ideal, elapsed, delay = line._delay_values(now)
            values = {'ideal_minutes': ideal, 'elapsed_minutes': elapsed,
                'delay': delay}
            for name in names:
                result[name][line.id] = values[name]
        return result

    @classmethod
    def _move(cls, lines, allowed, state):
        super()._move(lines, allowed, state)
        values = {'notified_level': 0}
        if state == 'ready':
            values['ready_at'] = datetime.datetime.now()
        with without_check_access():
            cls.write(lines, values)

    # Scheduled action

    @classmethod
    def notify_delays(cls):
        """Notify the kitchen, the bar and the waiters of late items.

        Entry point of the scheduled action: one pop-up per station and
        level, listing the items, sent once when the level is reached.
        Return the number of items notified."""
        now = datetime.datetime.now()
        lines = cls.search([('state', 'in', ['sent', 'preparing', 'ready'])])
        groups = defaultdict(list)
        to_mark = defaultdict(list)
        for line in lines:
            ideal, elapsed, delay = line._delay_values(now)
            level = {'warning': 1, 'late': 2}.get(delay, 0)
            if level and level > (line.notified_level or 0):
                target = 'serve' if line.state == 'ready' else line.station
                groups[(target, level)].append((line, ideal, elapsed))
                to_mark[level].append(line)
        count = 0
        for (target, level), items in sorted(groups.items()):
            cls._push(target, level, items)
            count += len(items)
        with without_check_access():
            for level, marked in to_mark.items():
                cls.write(marked, {'notified_level': level})
        return count

    @classmethod
    def _push(cls, target, level, items):
        from trytond import bus
        title = gettext(
            'restaurant.msg_delay_title_%s_%d' % (
                'serve' if target == 'serve' else 'prep', level),
            station=gettext('restaurant.msg_station_%s' % target)
            if target != 'serve' else '', count=len(items))
        body = '\n'.join(
            gettext('restaurant.msg_delay_item',
                where=line.table.rec_name if line.table
                else line.order.rec_name,
                product=line.product.rec_name, elapsed=elapsed, ideal=ideal)
            for line, ideal, elapsed in items[:10])
        if len(items) > 10:
            body += '\n…'
        users = cls._screen_users(
            'group_restaurant' if target == 'serve'
            else 'group_restaurant_kitchen')
        if level == 2:
            users |= cls._screen_users('group_restaurant_manager')
        for user in users:
            try:
                bus.notify(title, body, _PRIORITY[level], user=user)
            except Exception:  # never block the scheduler on a pop-up
                logger.warning("Cannot push notification", exc_info=True)

    @classmethod
    def _screen_users(cls, xml_id):
        pool = Pool()
        UserGroup = pool.get('res.user-res.group')
        ModelData = pool.get('ir.model.data')
        group = ModelData.get_id('restaurant', xml_id)
        return {ug.user.id for ug in UserGroup.search(
                [('group', '=', group)])}


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('restaurant.order.line|notify_delays',
                "Notify Kitchen and Bar Delays"))
