# This file is part of the Tryton hotel_pms module.
import datetime
import logging

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Unique, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

logger = logging.getLogger(__name__)

KINDS = [
    ('reminder', "Reminder"),
    ('alert', "Alert"),
    ('warning', "Warning"),
    ('info', "Information"),
    ]
# Priority of the pop-up shown in the client
_PRIORITY = {'alert': 3, 'warning': 2, 'reminder': 1, 'info': 0}
_PUSHED = {'alert', 'warning', 'reminder'}


class Notification(Workflow, ModelSQL, ModelView):
    "Hotel Notification"
    __name__ = 'hotel.notification'
    _rec_name = 'title'

    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True, ondelete='CASCADE',
        states={'readonly': Eval('state') != 'open'})
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    kind = fields.Selection(
        KINDS, "Kind", required=True,
        states={'readonly': Eval('automatic', False)})
    title = fields.Char("Title", required=True)
    message = fields.Text("Message")
    date = fields.DateTime("Date", required=True, readonly=True)
    due_date = fields.DateTime(
        "Due Date", help="When the reminder is due.")
    user = fields.Many2One(
        'res.user', "User",
        help="Restrict the notification to a user. Empty is for everybody.")
    origin = fields.Reference(
        "Origin", selection='get_origin', readonly=True)
    key = fields.Char(
        "Key", readonly=True,
        help="Identifies the condition that raised an automatic "
        "notification.")
    automatic = fields.Boolean("Automatic", readonly=True)
    state = fields.Selection([
            ('open', "Open"),
            ('done', "Done"),
            ('dismissed', "Dismissed"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('key_unique', Unique(t, t.hotel, t.key),
                'hotel_pms.msg_notification_key_unique'),
            ]
        cls._order = [('date', 'DESC'), ('id', 'DESC')]
        cls._transitions |= {
            ('open', 'done'), ('open', 'dismissed'),
            ('done', 'open'), ('dismissed', 'open'),
            }
        cls._buttons.update({
                'done': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                'dismiss': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                'reopen': {
                    'invisible': Eval('state') == 'open',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual', If(Eval('state') != 'open', 'muted', '')),
            ('/tree/field[@name="kind"]', 'visual',
                If(Eval('kind') == 'alert', 'danger',
                    If(Eval('kind') == 'warning', 'warning',
                        If(Eval('kind') == 'info', 'muted', '')))),
            ]

    @classmethod
    def get_origin(cls):
        return [
            (None, ""),
            ('hotel.reservation', "Reservation"),
            ('hotel.room', "Room"),
            ('stock.order_point', "Reorder Point"),
            ]

    @classmethod
    def default_kind(cls):
        return 'reminder'

    @classmethod
    def default_date(cls):
        return datetime.datetime.now()

    @classmethod
    def default_automatic(cls):
        return False

    @classmethod
    def default_state(cls):
        return 'open'

    @fields.depends('hotel')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def done(cls, notifications):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('dismissed')
    def dismiss(cls, notifications):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('open')
    def reopen(cls, notifications):
        pass

    # Automatic notifications

    @classmethod
    def generate(cls, hotels=None):
        """Raise the notifications of the hotels (cron entry point).

        Each rule returns the conditions currently true; new ones create a
        notification (and a pop-up), the open automatic notifications whose
        condition is gone are marked done. Dismissed notifications are not
        raised again for the same condition.
        """
        pool = Pool()
        Hotel = pool.get('hotel.hotel')
        if hotels is None:
            hotels = Hotel.search([])
        created = []
        for hotel in hotels:
            created.extend(cls._generate_hotel(hotel))
        cls._push(created)
        return created

    @classmethod
    def _generate_hotel(cls, hotel):
        now = datetime.datetime.now()
        current = {}
        for rule in cls._rules():
            for values in rule(hotel, now):
                current[values['key']] = values
        existing = {n.key: n for n in cls.search([
                    ('hotel', '=', hotel.id), ('automatic', '=', True)])}
        to_create = []
        for key, values in current.items():
            if key in existing:
                continue
            values.update(hotel=hotel.id, automatic=True, date=now)
            to_create.append(values)
        created = cls.create(to_create) if to_create else []
        resolved = [n for k, n in existing.items()
            if k not in current and n.state == 'open']
        if resolved:
            cls.done(resolved)
        return created

    @classmethod
    def _rules(cls):
        return [
            cls._rule_arrivals, cls._rule_departures, cls._rule_late_arrival,
            cls._rule_overdue_checkout, cls._rule_no_show,
            cls._rule_room_not_ready, cls._rule_unbilled, cls._rule_unpaid,
            cls._rule_low_stock, cls._rule_rooms_out, cls._rule_occupancy,
            ]

    @classmethod
    def _reservations(cls, hotel, domain):
        Reservation = Pool().get('hotel.reservation')
        return Reservation.search([('hotel', '=', hotel.id)] + domain)

    @classmethod
    def _rooms(cls, reservation):
        return ', '.join(line.room.rec_name for line in reservation.lines if line.room)

    @classmethod
    def _text(cls, name, **kwargs):
        return gettext('hotel_pms.' + name, **kwargs)

    @classmethod
    def _item(cls, kind, key, title, message, origin=None, due=None):
        return {
            'kind': kind, 'key': key, 'title': title, 'message': message,
            'origin': str(origin) if origin else None, 'due_date': due}

    @classmethod
    def _rule_arrivals(cls, hotel, now):
        low = datetime.datetime.combine(now.date(), datetime.time.min)
        high = now + datetime.timedelta(hours=hotel.notice_hours)
        for r in cls._reservations(hotel, [
                    ('state', '=', 'confirmed'),
                    ('start', '>=', low), ('start', '<=', high)]):
            yield cls._item(
                'reminder', 'arrival:%s:%s' % (r.id, r.start.date()),
                cls._text('msg_notif_arrival_title', number=r.number),
                cls._text('msg_notif_arrival', guest=r.party.rec_name,
                    rooms=cls._rooms(r),
                    time=r.start.strftime('%Y-%m-%d %H:%M')), r, r.start)

    @classmethod
    def _rule_departures(cls, hotel, now):
        high = now + datetime.timedelta(hours=hotel.notice_hours)
        for r in cls._reservations(hotel, [
                    ('state', '=', 'checked_in'),
                    ('end', '>=', now), ('end', '<=', high)]):
            yield cls._item(
                'reminder', 'departure:%s:%s' % (r.id, r.end.date()),
                cls._text('msg_notif_departure_title', number=r.number),
                cls._text('msg_notif_departure', guest=r.party.rec_name,
                    rooms=cls._rooms(r),
                    time=r.end.strftime('%Y-%m-%d %H:%M')), r, r.end)

    @classmethod
    def _rule_late_arrival(cls, hotel, now):
        grace = datetime.timedelta(hours=hotel.no_show_hours)
        for r in cls._reservations(hotel, [
                    ('state', '=', 'confirmed'),
                    ('start', '<', now), ('start', '>=', now - grace)]):
            yield cls._item(
                'warning', 'late:%s:%s' % (r.id, r.start.date()),
                cls._text('msg_notif_late_title', number=r.number),
                cls._text('msg_notif_late', guest=r.party.rec_name,
                    time=r.start.strftime('%H:%M')), r, r.start)

    @classmethod
    def _rule_overdue_checkout(cls, hotel, now):
        for r in cls._reservations(hotel, [
                    ('state', '=', 'checked_in'), ('end', '<', now)]):
            yield cls._item(
                'alert', 'overdue:%s' % r.id,
                cls._text('msg_notif_overdue_title', number=r.number),
                cls._text('msg_notif_overdue', guest=r.party.rec_name,
                    rooms=cls._rooms(r), time=r.end.strftime('%Y-%m-%d %H:%M')),
                r, r.end)

    @classmethod
    def _rule_no_show(cls, hotel, now):
        limit = now - datetime.timedelta(hours=hotel.no_show_hours)
        for r in cls._reservations(hotel, [
                    ('state', '=', 'confirmed'), ('start', '<', limit)]):
            yield cls._item(
                'alert', 'noshow:%s' % r.id,
                cls._text('msg_notif_noshow_title', number=r.number),
                cls._text('msg_notif_noshow', guest=r.party.rec_name,
                    time=r.start.strftime('%Y-%m-%d %H:%M')), r, r.start)

    @classmethod
    def _rule_room_not_ready(cls, hotel, now):
        low = datetime.datetime.combine(now.date(), datetime.time.min)
        high = now + datetime.timedelta(hours=hotel.notice_hours)
        for r in cls._reservations(hotel, [
                    ('state', '=', 'confirmed'),
                    ('start', '>=', low), ('start', '<=', high)]):
            for line in r.lines:
                room = line.room
                if room and room.state != 'available':
                    yield cls._item(
                        'warning', 'notready:%s:%s:%s' % (
                            r.id, room.id, r.start.date()),
                        cls._text('msg_notif_notready_title',
                            room=room.rec_name),
                        cls._text('msg_notif_notready', room=room.rec_name,
                            state=room.state, number=r.number,
                            time=r.start.strftime('%H:%M')), room, r.start)

    @classmethod
    def _rule_unbilled(cls, hotel, now):
        for r in cls._reservations(hotel, [
                    ('state', '=', 'checked_out'),
                    ('sale', '=', None), ('subscription', '=', None)]):
            yield cls._item(
                'warning', 'unbilled:%s' % r.id,
                cls._text('msg_notif_unbilled_title', number=r.number),
                cls._text('msg_notif_unbilled', guest=r.party.rec_name,
                    amount=r.total_amount), r)

    @classmethod
    def _rule_unpaid(cls, hotel, now):
        for r in cls._reservations(hotel, [
                    ('state', '=', 'checked_out'),
                    ('sale.invoice_state', 'in',
                        ['awaiting payment', 'partially paid'])]):
            yield cls._item(
                'warning', 'unpaid:%s' % r.id,
                cls._text('msg_notif_unpaid_title', number=r.number),
                cls._text('msg_notif_unpaid', guest=r.party.rec_name), r)

    @classmethod
    def _rule_low_stock(cls, hotel, now):
        pool = Pool()
        OrderPoint = pool.get('stock.order_point')
        Product = pool.get('product.product')
        points = OrderPoint.search([
                ('warehouse_location', '=', hotel.warehouse.id),
                ('type', '=', 'purchase'),
                ('product.template.hotel_article', '=', True)])
        if not points:
            return
        with Transaction().set_context(stock_date_end=now.date()):
            quantities = Product.products_by_location(
                [hotel.warehouse.id], with_childs=True,
                grouping_filter=([p.product.id for p in points],))
        for point in points:
            quantity = quantities.get(
                (hotel.warehouse.id, point.product.id), 0.)
            if quantity < (point.min_quantity or 0.):
                yield cls._item(
                    'warning', 'lowstock:%s' % point.id,
                    cls._text('msg_notif_lowstock_title',
                        product=point.product.rec_name),
                    cls._text('msg_notif_lowstock',
                        product=point.product.rec_name, quantity=quantity,
                        minimum=point.min_quantity), point)

    @classmethod
    def _rule_rooms_out(cls, hotel, now):
        Room = Pool().get('hotel.room')
        for room in Room.search([
                    ('hotel', '=', hotel.id),
                    ('state', 'in', ['maintenance', 'out_of_order'])]):
            yield cls._item(
                'info', 'roomout:%s:%s' % (room.id, room.state),
                cls._text('msg_notif_roomout_title', room=room.rec_name),
                cls._text('msg_notif_roomout', room=room.rec_name,
                    state=room.state), room)

    @classmethod
    def _rule_occupancy(cls, hotel, now):
        Room = Pool().get('hotel.room')
        total = Room.search_count([('hotel', '=', hotel.id)])
        occupied = Room.search_count([
                ('hotel', '=', hotel.id), ('state', '=', 'occupied')])
        if total and occupied * 100 >= 90 * total:
            yield cls._item(
                'info', 'occupancy:%s' % now.date(),
                cls._text('msg_notif_occupancy_title'),
                cls._text('msg_notif_occupancy', occupied=occupied,
                    total=total))

    @classmethod
    def _push(cls, notifications):
        "Pop-up in the client of the hotel users"
        if not notifications:
            return
        try:
            from trytond import bus
            pool = Pool()
            UserGroup = pool.get('res.user-res.group')
            Group = pool.get('res.group')
            ModelData = pool.get('ir.model.data')
            group = Group(ModelData.get_id('hotel', 'group_hotel'))
            users = {ug.user.id for ug in UserGroup.search(
                    [('group', '=', group.id)])}
            for notification in notifications:
                if notification.kind not in _PUSHED:
                    continue
                targets = ({notification.user.id} if notification.user
                    else users)
                for user in targets:
                    bus.notify(
                        notification.title, notification.message,
                        _PRIORITY[notification.kind], user=user)
        except Exception:  # never block the cron on a pop-up
            logger.warning("Cannot push notifications", exc_info=True)

    @classmethod
    def copy(cls, notifications, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('key', None)
        default.setdefault('automatic', False)
        default.setdefault('state', 'open')
        return super().copy(notifications, default=default)


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    notice_hours = fields.Integer(
        "Notice Hours", required=True,
        domain=[('notice_hours', '>=', 1)],
        help="Arrivals and departures are notified this many hours ahead.")
    no_show_hours = fields.Integer(
        "No-show Hours", required=True,
        domain=[('no_show_hours', '>=', 0)],
        help="A confirmed reservation not checked in this many hours after "
        "its arrival is a no-show.")

    @classmethod
    def default_notice_hours(cls):
        return 24

    @classmethod
    def default_no_show_hours(cls):
        return 3


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('hotel.notification|generate', "Generate Hotel Notifications"))


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    @fields.depends('adults', 'children', 'start', 'end', 'lines')
    def on_change_notify(self):
        "Warn while editing: capacity exceeded, room already booked"
        notifications = super().on_change_notify()
        capacity = 0
        for line in self.lines or []:
            if getattr(line, 'room_type', None):
                capacity += line.room_type.capacity or 0
        guests = (self.adults or 0) + (self.children or 0)
        if capacity and guests > capacity:
            notifications.append(('warning', gettext(
                        'hotel_pms.msg_notice_capacity',
                        guests=guests, capacity=capacity)))
        if self.start and self.end:
            exclude = self if (self.id or -1) >= 0 else None
            for line in self.lines or []:
                room = getattr(line, 'room', None)
                if room and not room.is_available(
                        self.start, self.end, exclude=exclude):
                    notifications.append(('warning', gettext(
                                'hotel_pms.msg_notice_room_busy',
                                room=room.rec_name)))
        return notifications
