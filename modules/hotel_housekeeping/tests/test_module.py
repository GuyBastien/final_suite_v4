# This file is part of the Tryton hotel_housekeeping module.
import datetime

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import HousekeepingError


class HousekeepingTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_housekeeping module"
    module = 'hotel_housekeeping'

    def _setup(self, company):
        pool = Pool()
        Room = pool.get('hotel.room')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room1, room2 = Room.create([
                {'hotel': hotel.id, 'name': n, 'type': room_type.id}
                for n in ('101', '102')])
        return hotel, room_type, room1, room2

    def _task(self, hotel, room, **values):
        Task = Pool().get('hotel.housekeeping.task')
        values.update(hotel=hotel.id, room=room.id)
        return Task.create([values])[0]

    @with_transaction()
    def test_checklist_copied_and_required(self):
        "Test the hotel checklist is copied and blocks finishing"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Task = pool.get('hotel.housekeeping.task')
            Checklist = pool.get('hotel.housekeeping.checklist')
            hotel, _, room, _ = self._setup(company)
            Checklist.create([
                    {'hotel': hotel.id, 'name': "Change linen",
                        'required': True},
                    {'hotel': hotel.id, 'name': "Restock minibar",
                        'required': False, 'kind': 'cleaning'},
                    {'hotel': hotel.id, 'name': "Check the safe",
                        'kind': 'deep'},
                    ])
            room.state = 'dirty'
            room.save()
            task = self._task(hotel, room)
            self.assertEqual(
                sorted(i.name for i in task.items),
                ["Change linen", "Restock minibar"])
            with self.assertRaises(HousekeepingError):
                Task.finish([task])
            for item in task.items:
                if item.required:
                    item.done = True
                    item.save()
            Task.finish([task])
            self.assertEqual(task.state, 'done')
            self.assertEqual(room.state, 'available')

    @with_transaction()
    def test_workflow_and_inspection(self):
        "Test the room is released after inspection when required"
        company = create_company()
        with set_company(company):
            Task = Pool().get('hotel.housekeeping.task')
            hotel, _, room, _ = self._setup(company)
            hotel.inspection_required = True
            hotel.save()
            room.state = 'dirty'
            room.save()
            task = self._task(hotel, room)
            Task.take([task])
            self.assertEqual(task.assigned_to.id, Transaction().user)
            self.assertTrue(task.mine)
            Task.start([task])
            self.assertEqual(task.state, 'in_progress')
            self.assertTrue(task.started_at)
            Task.finish([task])
            self.assertEqual(room.state, 'dirty')
            Task.redo([task])
            self.assertEqual(task.state, 'todo')
            Task.finish([task])
            Task.inspect([task])
            self.assertEqual(task.state, 'inspected')
            self.assertEqual(room.state, 'available')
            with self.assertRaises(HousekeepingError):
                Task.delete([task])

    @with_transaction()
    def test_mine_search_and_cancel(self):
        "Test search on assigned tasks and cancel"
        company = create_company()
        with set_company(company):
            Task = Pool().get('hotel.housekeeping.task')
            hotel, _, room1, room2 = self._setup(company)
            mine = self._task(
                hotel, room1, assigned_to=Transaction().user)
            other = self._task(hotel, room2)
            self.assertEqual(Task.search([('mine', '=', True)]), [mine])
            self.assertEqual(Task.search([('mine', '=', False)]), [other])
            Task.cancel([other])
            Task.reopen([other])
            Task.delete([other])

    @with_transaction()
    def test_room_must_belong_to_hotel(self):
        "Test the room belongs to the hotel of the task"
        company = create_company()
        with set_company(company):
            hotel, _, room, _ = self._setup(company)
            other, = Pool().get('hotel.hotel').create([{
                        'name': "Other", 'code': "OH",
                        'company': company.id,
                        'warehouse': hotel.warehouse.id,
                        'consumption_location':
                        hotel.consumption_location.id}])
            with self.assertRaises(Exception):
                self._task(other, room)

    @with_transaction()
    def test_issue_blocks_and_resolves(self):
        "Test a blocking issue takes the room out of service"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Issue = pool.get('hotel.housekeeping.issue')
            hotel, _, room, _ = self._setup(company)
            task = self._task(hotel, room)
            issue, = Issue.create([{
                        'task': task.id, 'severity': 'blocking',
                        'description': "Broken shower"}])
            self.assertEqual(issue.room, room)
            self.assertEqual(issue.hotel, hotel)
            self.assertEqual(room.state, 'maintenance')
            with self.assertRaises(HousekeepingError):
                Issue.resolve([issue])
            issue.resolution = "Plumber came"
            issue.save()
            Issue.resolve([issue])
            self.assertEqual(issue.state, 'resolved')
            self.assertEqual(room.state, 'dirty')
            Issue.reopen([issue])
            self.assertEqual(room.state, 'maintenance')

    @with_transaction()
    def test_generate_tasks(self):
        "Test the cron creates cleaning and refresh tasks once"
        company = create_company()
        with set_company(company):
            Task = Pool().get('hotel.housekeeping.task')
            hotel, _, room1, room2 = self._setup(company)
            hotel.daily_refresh = True
            hotel.save()
            room1.state = 'dirty'
            room1.save()
            room2.state = 'occupied'
            room2.save()
            created = Task.generate([hotel])
            self.assertEqual(
                sorted((t.room.name, t.kind) for t in created),
                [('101', 'cleaning'), ('102', 'refresh')])
            self.assertEqual(Task.generate([hotel]), [])

    @with_transaction()
    def test_check_out_creates_task_and_notifications(self):
        "Test check-out raises a cleaning task and rules notify"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Task = pool.get('hotel.housekeeping.task')
            Issue = pool.get('hotel.housekeeping.issue')
            Notification = pool.get('hotel.notification')
            Reservation = pool.get('hotel.reservation')
            Party = pool.get('party.party')
            hotel, room_type, room, _ = self._setup(company)
            room_type.night_product = None
            party, = Party.create([{'name': "Guest"}])
            now = datetime.datetime.now().replace(microsecond=0)
            reservation, = Reservation.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'party': party.id,
                        'start': now - datetime.timedelta(hours=1),
                        'end': now + datetime.timedelta(days=1),
                        'lines': [('create', [{
                                        'room_type': room_type.id,
                                        'room': room.id}])]}])
            Reservation.confirm([reservation])
            Reservation.check_in([reservation])
            Reservation.check_out([reservation])
            task, = Task.search([('room', '=', room.id)])
            self.assertEqual(task.kind, 'cleaning')
            self.assertEqual(task.reservation, reservation)
            Issue.create([{
                        'room': room.id, 'severity': 'major',
                        'description': "Stained carpet"}])
            task.planned_date = datetime.date.today() - datetime.timedelta(
                days=1)
            task.save()
            Notification.generate([hotel])
            keys = {n.key.split(':')[0] for n in Notification.search([
                        ('hotel', '=', hotel.id), ('state', '=', 'open')])}
            self.assertTrue({'hkissue', 'hktask'} <= keys)
            # finished task resolves its warning
            Task.finish([task])
            Notification.generate([hotel])
            keys = {n.key.split(':')[0] for n in Notification.search([
                        ('hotel', '=', hotel.id), ('state', '=', 'open')])}
            self.assertNotIn('hktask', keys)


del ModuleTestCase
