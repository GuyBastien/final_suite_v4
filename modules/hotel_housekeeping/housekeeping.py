# This file is part of the Tryton hotel_housekeeping module.
import datetime

from trytond.i18n import gettext
from trytond.model import (
    ModelSQL, ModelView, Workflow, fields, sequence_ordered)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

from .exceptions import HousekeepingError

KINDS = [
    ('cleaning', "Departure Cleaning"),
    ('refresh', "Stay-over Refresh"),
    ('turndown', "Turndown"),
    ('deep', "Deep Cleaning"),
    ('inspection', "Inspection"),
    ]
# Kinds that make a dirty room available again
ROOM_KINDS = {'cleaning', 'deep'}
PRIORITIES = [
    ('low', "Low"),
    ('normal', "Normal"),
    ('high', "High"),
    ('urgent', "Urgent"),
    ]
SEVERITIES = [
    ('minor', "Minor"),
    ('major', "Major"),
    ('blocking', "Blocking"),
    ]
OPEN_STATES = ['todo', 'in_progress']


def _label(record, name):
    "Translated label of a selection value"
    selection = record.fields_get([name])[name]['selection']
    return dict(selection).get(getattr(record, name), '')


def _no_access():
    "Room state changes made by the staff are not restricted to Room rights"
    return Transaction().set_context(_check_access=False)


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    inspection_required = fields.Boolean(
        "Inspection Required",
        help="If checked, a cleaned room becomes available only once "
        "a supervisor has inspected it.")
    daily_refresh = fields.Boolean(
        "Daily Refresh Tasks",
        help="Create a stay-over refresh task every day for the occupied "
        "rooms.")
    checklist = fields.One2Many(
        'hotel.housekeeping.checklist', 'hotel', "Checklist",
        help="The items copied on each new housekeeping task.")

    @classmethod
    def default_inspection_required(cls):
        return False

    @classmethod
    def default_daily_refresh(cls):
        return False


class ChecklistTemplate(sequence_ordered(), ModelSQL, ModelView):
    "Hotel Housekeeping Checklist Item"
    __name__ = 'hotel.housekeeping.checklist'

    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True, ondelete='CASCADE')
    name = fields.Char("Name", required=True, translate=True)
    kind = fields.Selection(
        [(None, "")] + KINDS, "Kind",
        help="Empty applies the item to every kind of task.")
    required = fields.Boolean(
        "Required",
        help="The task cannot be finished while this item is not done.")

    @classmethod
    def default_required(cls):
        return True


class Task(Workflow, ModelSQL, ModelView):
    "Hotel Housekeeping Task"
    __name__ = 'hotel.housekeeping.task'
    _rec_name = 'name'

    name = fields.Function(
        fields.Char("Name"), 'get_name', searcher='search_name')
    hotel = fields.Many2One(
        'hotel.hotel', "Hotel", required=True, ondelete='CASCADE',
        states={'readonly': Eval('state') != 'todo'})
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'on_change_with_company', searcher='search_company')
    room = fields.Many2One(
        'hotel.room', "Room", required=True, ondelete='CASCADE',
        domain=[('hotel', '=', Eval('hotel', -1))],
        states={'readonly': Eval('state') != 'todo'})
    kind = fields.Selection(
        KINDS, "Kind", required=True,
        states={'readonly': Eval('state') != 'todo'})
    priority = fields.Selection(PRIORITIES, "Priority", required=True)
    planned_date = fields.Date(
        "Planned Date", required=True,
        states={'readonly': ~Eval('state').in_(OPEN_STATES)})
    assigned_to = fields.Many2One(
        'res.user', "Assigned To",
        states={'readonly': ~Eval('state').in_(OPEN_STATES)})
    mine = fields.Function(
        fields.Boolean("Assigned to Me"), 'get_mine', searcher='search_mine')
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", readonly=True,
        ondelete='SET NULL',
        help="The stay whose check-out raised the task.")
    started_at = fields.DateTime("Started At", readonly=True)
    finished_at = fields.DateTime("Finished At", readonly=True)
    notes = fields.Text("Notes")
    items = fields.One2Many('hotel.housekeeping.task.item', 'task', "Checklist")
    issues = fields.One2Many('hotel.housekeeping.issue', 'task', "Issues")
    state = fields.Selection([
            ('todo', "To Do"),
            ('in_progress', "In Progress"),
            ('done', "Done"),
            ('inspected', "Inspected"),
            ('cancelled', "Cancelled"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('planned_date', 'DESC'), ('id', 'DESC')]
        cls._transitions |= {
            ('todo', 'in_progress'),
            ('todo', 'done'),
            ('in_progress', 'done'),
            ('done', 'inspected'),
            ('done', 'todo'),
            ('todo', 'cancelled'),
            ('in_progress', 'cancelled'),
            ('cancelled', 'todo'),
            }
        cls._buttons.update({
                'take': {
                    'invisible': (~Eval('state').in_(OPEN_STATES)
                        | (Eval('assigned_to', -1) == Eval('_user', -1))),
                    'depends': ['state', 'assigned_to'],
                    },
                'start': {
                    'invisible': Eval('state') != 'todo',
                    'depends': ['state'],
                    },
                'finish': {
                    'invisible': ~Eval('state').in_(OPEN_STATES),
                    'depends': ['state'],
                    },
                'inspect': {
                    'invisible': Eval('state') != 'done',
                    'depends': ['state'],
                    },
                'redo': {
                    'invisible': Eval('state') != 'done',
                    'depends': ['state'],
                    },
                'cancel': {
                    'invisible': ~Eval('state').in_(OPEN_STATES),
                    'depends': ['state'],
                    },
                'reopen': {
                    'invisible': Eval('state') != 'cancelled',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state').in_(['done', 'inspected', 'cancelled']),
                    'muted',
                    If(Eval('priority') == 'urgent', 'danger',
                        If(Eval('priority') == 'high', 'warning', '')))),
            ]

    @classmethod
    def default_kind(cls):
        return 'cleaning'

    @classmethod
    def default_priority(cls):
        return 'normal'

    @classmethod
    def default_planned_date(cls):
        return datetime.date.today()

    @classmethod
    def default_state(cls):
        return 'todo'

    @classmethod
    def default_hotel(cls):
        hotels = Pool().get('hotel.hotel').search([], limit=2)
        if len(hotels) == 1:
            return hotels[0].id

    def get_name(self, name):
        return "%s - %s" % (self.room.rec_name, _label(self, 'kind'))

    @classmethod
    def search_name(cls, name, clause):
        return [('room.rec_name',) + tuple(clause[1:])]

    @fields.depends('hotel')
    def on_change_with_company(self, name=None):
        if self.hotel:
            return self.hotel.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('hotel.company',) + tuple(clause[1:])]

    def get_mine(self, name):
        return self.assigned_to is not None and (
            self.assigned_to.id == Transaction().user)

    @classmethod
    def search_mine(cls, name, clause):
        _, operator, value = clause
        if operator in {'=', '!='} and bool(value) == (operator == '='):
            return [('assigned_to', '=', Transaction().user)]
        return ['OR', ('assigned_to', '!=', Transaction().user),
            ('assigned_to', '=', None)]

    @classmethod
    def validate(cls, tasks):
        super().validate(tasks)
        for task in tasks:
            if task.room.hotel != task.hotel:
                raise HousekeepingError(gettext(
                        'hotel_housekeeping.msg_task_room_hotel',
                        room=task.room.rec_name, task=task.rec_name))

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Item = pool.get('hotel.housekeeping.task.item')
        Template = pool.get('hotel.housekeeping.checklist')
        tasks = super().create(vlist)
        items = []
        for task, values in zip(tasks, vlist):
            if values.get('items'):
                continue
            for template in Template.search([
                        ('hotel', '=', task.hotel.id),
                        ['OR', ('kind', '=', None),
                            ('kind', '=', task.kind)]]):
                items.append({
                        'task': task.id, 'sequence': template.sequence,
                        'name': template.name, 'required': template.required,
                        })
        if items:
            Item.create(items)
        return tasks

    @classmethod
    def delete(cls, tasks):
        for task in tasks:
            if task.state not in {'todo', 'cancelled'}:
                raise HousekeepingError(gettext(
                        'hotel_housekeeping.msg_task_delete_state',
                        task=task.rec_name))
        super().delete(tasks)

    # Generation

    @classmethod
    def _priority(cls, hotel, room, now):
        Reservation = Pool().get('hotel.reservation')
        limit = now + datetime.timedelta(hours=hotel.notice_hours)
        if Reservation.search([
                    ('hotel', '=', hotel.id), ('state', '=', 'confirmed'),
                    ('start', '<=', limit), ('lines.room', '=', room.id),
                    ], limit=1):
            return 'high'
        return 'normal'

    @classmethod
    def create_for_rooms(cls, rooms, kind, reservation=None, date=None):
        "Create a task per room unless an equivalent one already exists"
        now = datetime.datetime.now()
        date = date or now.date()
        vlist = []
        for room in rooms:
            domain = [
                ('room', '=', room.id), ('kind', '=', kind),
                ('state', '!=', 'cancelled')]
            if kind == 'cleaning':
                domain.append(('state', 'in', OPEN_STATES))
            else:
                domain.append(('planned_date', '=', date))
            if cls.search(domain, limit=1):
                continue
            vlist.append({
                    'hotel': room.hotel.id, 'room': room.id, 'kind': kind,
                    'planned_date': date,
                    'priority': cls._priority(room.hotel, room, now),
                    'reservation': reservation.id if reservation else None,
                    })
        return cls.create(vlist) if vlist else []

    @classmethod
    def generate(cls, hotels=None):
        "Create the missing tasks (cron entry point)"
        pool = Pool()
        Hotel = pool.get('hotel.hotel')
        Room = pool.get('hotel.room')
        created = []
        for hotel in hotels if hotels is not None else Hotel.search([]):
            dirty = Room.search([
                    ('hotel', '=', hotel.id), ('state', '=', 'dirty')])
            created += cls.create_for_rooms(dirty, 'cleaning')
            if hotel.daily_refresh:
                occupied = Room.search([
                        ('hotel', '=', hotel.id), ('state', '=', 'occupied')])
                created += cls.create_for_rooms(occupied, 'refresh')
        return created

    # Workflow

    def _check_items(self):
        if any(i.required and not i.done for i in self.items):
            raise HousekeepingError(gettext(
                    'hotel_housekeeping.msg_task_checklist_incomplete',
                    task=self.rec_name))

    @classmethod
    def _make_available(cls, tasks):
        Room = Pool().get('hotel.room')
        rooms = [t.room for t in tasks
            if t.kind in ROOM_KINDS and t.room.state == 'dirty']
        if rooms:
            with _no_access():
                Room.write(rooms, {'state': 'available'})

    @classmethod
    @ModelView.button
    def take(cls, tasks):
        cls.write(tasks, {'assigned_to': Transaction().user})

    @classmethod
    @ModelView.button
    @Workflow.transition('in_progress')
    def start(cls, tasks):
        now = datetime.datetime.now()
        cls.write(tasks, {'started_at': now})
        cls.take([t for t in tasks if not t.assigned_to])

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def finish(cls, tasks):
        now = datetime.datetime.now()
        for task in tasks:
            task._check_items()
        for task in tasks:
            values = {'finished_at': now}
            if not task.started_at:
                values['started_at'] = now
            if not task.assigned_to:
                values['assigned_to'] = Transaction().user
            cls.write([task], values)
        cls._make_available(
            [t for t in tasks if not t.hotel.inspection_required])

    @classmethod
    @ModelView.button
    @Workflow.transition('inspected')
    def inspect(cls, tasks):
        cls._make_available(tasks)

    @classmethod
    @ModelView.button
    @Workflow.transition('todo')
    def redo(cls, tasks):
        cls.write(tasks, {'finished_at': None})

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, tasks):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('todo')
    def reopen(cls, tasks):
        pass


class TaskItem(sequence_ordered(), ModelSQL, ModelView):
    "Hotel Housekeeping Task Item"
    __name__ = 'hotel.housekeeping.task.item'

    task = fields.Many2One(
        'hotel.housekeeping.task', "Task", required=True, ondelete='CASCADE')
    name = fields.Char("Name", required=True)
    required = fields.Boolean("Required")
    done = fields.Boolean("Done")

    @classmethod
    def default_required(cls):
        return True

    @classmethod
    def default_done(cls):
        return False


class Issue(ModelSQL, ModelView):
    "Hotel Housekeeping Issue"
    __name__ = 'hotel.housekeeping.issue'
    _rec_name = 'description'

    task = fields.Many2One(
        'hotel.housekeeping.task', "Task", ondelete='SET NULL')
    room = fields.Many2One(
        'hotel.room', "Room", ondelete='CASCADE',
        states={'required': ~Eval('task')})
    hotel = fields.Function(
        fields.Many2One('hotel.hotel', "Hotel"), 'get_hotel',
        searcher='search_hotel')
    company = fields.Function(
        fields.Many2One('company.company', "Company"), 'get_company',
        searcher='search_company')
    severity = fields.Selection(SEVERITIES, "Severity", required=True)
    description = fields.Char("Description", required=True)
    resolution = fields.Text(
        "Resolution", states={'readonly': Eval('state') != 'open'})
    reported_by = fields.Many2One('res.user', "Reported By", readonly=True)
    reported_at = fields.DateTime("Reported At", readonly=True)
    resolved_at = fields.DateTime("Resolved At", readonly=True)
    state = fields.Selection([
            ('open', "Open"),
            ('resolved', "Resolved"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('reported_at', 'DESC'), ('id', 'DESC')]
        cls._buttons.update({
                'resolve': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state'],
                    },
                'reopen': {
                    'invisible': Eval('state') != 'resolved',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree/field[@name="severity"]', 'visual',
                If(Eval('severity') == 'blocking', 'danger',
                    If(Eval('severity') == 'major', 'warning', ''))),
            ]

    @classmethod
    def default_severity(cls):
        return 'minor'

    @classmethod
    def default_state(cls):
        return 'open'

    @classmethod
    def default_reported_by(cls):
        return Transaction().user

    @classmethod
    def default_reported_at(cls):
        return datetime.datetime.now()

    def get_hotel(self, name):
        return self.room.hotel.id

    def get_company(self, name):
        return self.room.hotel.company.id

    @classmethod
    def search_hotel(cls, name, clause):
        return [('room.hotel',) + tuple(clause[1:])]

    @classmethod
    def search_company(cls, name, clause):
        return [('room.hotel.company',) + tuple(clause[1:])]

    @classmethod
    def create(cls, vlist):
        Task = Pool().get('hotel.housekeeping.task')
        vlist = [v.copy() for v in vlist]
        for values in vlist:
            if not values.get('room') and values.get('task'):
                values['room'] = Task(values['task']).room.id
        issues = super().create(vlist)
        cls._block_rooms([i for i in issues if i.severity == 'blocking'])
        return issues

    @classmethod
    def write(cls, *args):
        super().write(*args)
        issues = sum(args[0::2], [])
        cls._block_rooms([
                i for i in issues
                if i.severity == 'blocking' and i.state == 'open'])

    @classmethod
    def _block_rooms(cls, issues):
        "A blocking issue takes the free room out of the inventory"
        Room = Pool().get('hotel.room')
        rooms = list({i.room for i in issues
                if i.room.state in {'available', 'dirty'}})
        if rooms:
            with _no_access():
                Room.write(rooms, {'state': 'maintenance'})

    @classmethod
    @ModelView.button
    def resolve(cls, issues):
        Room = Pool().get('hotel.room')
        for issue in issues:
            if not issue.resolution:
                raise HousekeepingError(gettext(
                        'hotel_housekeeping.msg_issue_resolution_required',
                        issue=issue.rec_name))
        cls.write(issues, {
                'state': 'resolved', 'resolved_at': datetime.datetime.now()})
        # Repaired rooms must be cleaned before they are sold again
        rooms = []
        for issue in issues:
            room = issue.room
            if room.state == 'maintenance' and not cls.search_count([
                        ('room', '=', room.id), ('state', '=', 'open'),
                        ('severity', '=', 'blocking')]):
                rooms.append(room)
        if rooms:
            with _no_access():
                Room.write(rooms, {'state': 'dirty'})

    @classmethod
    @ModelView.button
    def reopen(cls, issues):
        cls.write(issues, {'state': 'open', 'resolved_at': None})


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.method.selection.append(
            ('hotel.housekeeping.task|generate',
                "Generate Housekeeping Tasks"))


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_out')
    def check_out(cls, reservations):
        Task = Pool().get('hotel.housekeeping.task')
        super().check_out(reservations)
        for reservation in reservations:
            Task.create_for_rooms(
                [line.room for line in reservation.lines if line.room], 'cleaning',
                reservation=reservation)


class Notification(metaclass=PoolMeta):
    __name__ = 'hotel.notification'

    @classmethod
    def _rules(cls):
        return super()._rules() + [
            cls._rule_hk_issues, cls._rule_hk_late]

    @classmethod
    def _rule_hk_issues(cls, hotel, now):
        Issue = Pool().get('hotel.housekeeping.issue')
        for issue in Issue.search([
                    ('room.hotel', '=', hotel.id), ('state', '=', 'open')]):
            yield cls._item(
                'alert' if issue.severity == 'blocking' else 'warning',
                'hkissue:%s' % issue.id,
                cls._housekeeping_text(
                    'msg_hk_issue_title', room=issue.room.rec_name),
                cls._housekeeping_text(
                    'msg_hk_issue', severity=_label(issue, 'severity'),
                    description=issue.description),
                issue.room, issue.reported_at)

    @classmethod
    def _rule_hk_late(cls, hotel, now):
        Task = Pool().get('hotel.housekeeping.task')
        for task in Task.search([
                    ('hotel', '=', hotel.id), ('state', 'in', OPEN_STATES),
                    ('planned_date', '<', now.date())]):
            yield cls._item(
                'warning', 'hktask:%s' % task.id,
                cls._housekeeping_text(
                    'msg_hk_task_late_title', room=task.room.rec_name),
                cls._housekeeping_text(
                    'msg_hk_task_late', task=task.rec_name,
                    date=task.planned_date.isoformat()),
                task.room, None)

    @classmethod
    def _housekeeping_text(cls, name, **kwargs):
        return gettext('hotel_housekeeping.' + name, **kwargs)
