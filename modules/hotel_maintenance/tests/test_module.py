# This file is part of the Tryton hotel_maintenance module.
import datetime
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import MaintenanceError

TODAY = datetime.date(2030, 3, 4)


class MaintenanceTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_maintenance module"
    module = 'hotel_maintenance'

    def _setup(self, company):
        pool = Pool()
        Room = pool.get('hotel.room')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room, = Room.create([{
                    'hotel': hotel.id, 'name': "101", 'type': room_type.id}])
        return hotel, room

    def _user(self):
        return Pool().get('res.user')(Transaction().user)

    @with_transaction()
    def test_order_workflow_blocks_room(self):
        "Test a work order takes the room out of service and releases it"
        company = create_company()
        with set_company(company):
            Order = Pool().get('hotel.maintenance.order')
            hotel, room = self._setup(company)
            order, = Order.create([{
                        'hotel': hotel.id, 'room': room.id,
                        'title': "Leaking tap", 'block_room': True}])
            self.assertTrue(order.number)
            self.assertEqual(order.state, 'open')
            self.assertEqual(order.priority, 'normal')
            # Assigning needs somebody
            with self.assertRaises(MaintenanceError):
                Order.assign([order])
            order.assigned_to = self._user()
            order.save()
            Order.assign([order])
            self.assertEqual(order.state, 'assigned')
            Order.start([order])
            self.assertEqual(order.state, 'in_progress')
            self.assertIsNotNone(order.started_at)
            self.assertEqual(order.room.state, 'maintenance')
            with self.assertRaises(MaintenanceError):
                Order.delete([order])
            Order.finish([order])
            self.assertEqual(order.state, 'done')
            self.assertIsNotNone(order.finished_at)
            self.assertEqual(order.room.state, 'dirty')

    @with_transaction()
    def test_urgent_order_out_of_order_and_cancel(self):
        "Test an urgent order puts the room out of order until cancelled"
        company = create_company()
        with set_company(company):
            Order = Pool().get('hotel.maintenance.order')
            hotel, room = self._setup(company)
            order, = Order.create([{
                        'hotel': hotel.id, 'room': room.id,
                        'title': "Flood", 'priority': 'urgent',
                        'block_room': True}])
            Order.start([order])
            self.assertEqual(order.room.state, 'out_of_order')
            Order.cancel([order])
            self.assertEqual(order.state, 'cancelled')
            self.assertEqual(order.room.state, 'dirty')
            Order.reopen([order])
            self.assertEqual(order.state, 'open')
            self.assertIsNone(order.started_at)
            Order.delete([order])

    @with_transaction()
    def test_order_occupied_room(self):
        "Test the work cannot block an occupied room"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('hotel.maintenance.order')
            Room = pool.get('hotel.room')
            hotel, room = self._setup(company)
            Room.write([room], {'state': 'occupied'})
            order, = Order.create([{
                        'hotel': hotel.id, 'room': room.id,
                        'title': "Paint", 'block_room': True}])
            with self.assertRaises(MaintenanceError):
                Order.start([order])
            # Without blocking, the work can start
            order.block_room = False
            order.save()
            Order.start([order])
            self.assertEqual(order.room.state, 'occupied')

    @with_transaction()
    def test_order_room_must_belong_to_hotel(self):
        "Test the room of an order belongs to its hotel"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Order = pool.get('hotel.maintenance.order')
            Hotel = pool.get('hotel.hotel')
            hotel, room = self._setup(company)
            other, = Hotel.create([{
                        'name': "Other", 'code': "OH",
                        'company': company.id,
                        'warehouse': hotel.warehouse.id,
                        'consumption_location': hotel.consumption_location.id,
                        }])
            with self.assertRaises(Exception):
                Order.create([{
                            'hotel': other.id, 'room': room.id,
                            'title': "Wrong"}])

    @with_transaction()
    def test_preventive_schedule(self):
        "Test the schedule creates its orders once and moves on"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Schedule = pool.get('hotel.maintenance.schedule')
            Order = pool.get('hotel.maintenance.order')
            hotel, room = self._setup(company)
            schedule, = Schedule.create([{
                        'hotel': hotel.id, 'room': room.id,
                        'title': "Air conditioning filter",
                        'every_days': 30, 'next_date': TODAY}])
            self.assertEqual(Schedule.generate(date=TODAY - datetime.timedelta(
                        days=1)), [])
            orders = Schedule.generate(date=TODAY)
            self.assertEqual(len(orders), 1)
            order, = orders
            self.assertEqual(order.planned_date, TODAY)
            self.assertEqual(order.schedule, schedule)
            self.assertEqual(order.room, room)
            schedule = Schedule(schedule.id)
            self.assertEqual(
                schedule.next_date, TODAY + datetime.timedelta(days=30))
            # Running again the same day creates nothing
            self.assertEqual(Schedule.generate(date=TODAY), [])
            self.assertEqual(len(Order.search([])), 1)
            # A late run catches up without creating duplicates
            orders = Schedule.generate(date=TODAY + datetime.timedelta(
                    days=65))
            self.assertEqual(len(orders), 1)
            self.assertEqual(
                Schedule(schedule.id).next_date,
                TODAY + datetime.timedelta(days=90))

    @with_transaction()
    def test_lost_and_found(self):
        "Test the lost and found workflow"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Item = pool.get('hotel.lost.item')
            Party = pool.get('party.party')
            hotel, room = self._setup(company)
            item, = Item.create([{
                        'hotel': hotel.id, 'room': room.id,
                        'description': "Black umbrella"}])
            self.assertTrue(item.number)
            self.assertEqual(item.state, 'stored')
            with self.assertRaises(MaintenanceError):
                Item.claim([item])
            owner, = Party.create([{'name': "Owner"}])
            item.claimed_by = owner
            item.save()
            Item.claim([item])
            self.assertEqual(item.state, 'claimed')
            self.assertIsNotNone(item.claim_date)
            other, = Item.create([{
                        'hotel': hotel.id, 'description': "Scarf"}])
            Item.dispose([other])
            self.assertEqual(other.state, 'disposed')
            self.assertNotEqual(other.number, item.number)

    def _linen(self):
        pool = Pool()
        Template = pool.get('product.template')
        Uom = pool.get('product.uom')
        unit, = Uom.search([('name', '=', "Unit")])
        template, = Template.create([{
                    'name': "Bath towel", 'type': 'goods',
                    'default_uom': unit.id, 'list_price': Decimal('5'),
                    'hotel_article': True, 'hotel_category': 'linen',
                    'products': [('create', [{'cost_price': Decimal('2')}])],
                    }])
        return template.products[0]

    @with_transaction()
    def test_laundry_batch(self):
        "Test the laundry moves the linen out and back, losses included"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Batch = pool.get('hotel.laundry.batch')
            Location = pool.get('stock.location')
            Move = pool.get('stock.move')
            Product = pool.get('product.product')
            Party = pool.get('party.party')
            hotel, room = self._setup(company)
            today = pool.get('ir.date').today()
            product = self._linen()
            unit = product.default_uom
            supplier, = Party.create([{'name': "Clean Laundry"}])
            batch, = Batch.create([{
                        'hotel': hotel.id, 'supplier': supplier.id,
                        'date': today,
                        'lines': [('create', [{
                                        'product': product.id,
                                        'unit': unit.id,
                                        'qty_sent': 10.}])]}])
            self.assertTrue(batch.number)
            # The hotel needs a laundry location
            with self.assertRaises(MaintenanceError):
                Batch.send([batch])
            laundry, = Location.create([{
                        'name': "Laundry", 'type': 'storage'}])
            hotel.laundry_location = laundry
            hotel.save()
            # Stock the linen in the warehouse
            supplier_loc, = Location.create([{
                        'name': "Supplier", 'type': 'supplier'}])
            receipt, = Move.create([{
                        'product': product.id, 'unit': unit.id,
                        'quantity': 10., 'from_location': supplier_loc.id,
                        'to_location': hotel.warehouse.storage_location.id,
                        'company': company.id,
                        'currency': company.currency.id,
                        'unit_price': Decimal('2'),
                        'effective_date': today}])
            with Transaction().set_context(_skip_warnings=True):
                Move.do([receipt])

            def quantity(location):
                with Transaction().set_context(stock_date_end=today):
                    return Product.products_by_location(
                        [location.id], with_childs=False,
                        grouping_filter=([product.id],)
                        ).get((location.id, product.id), 0.)

            Batch.send([batch])
            self.assertEqual(batch.state, 'sent')
            self.assertEqual(
                quantity(hotel.warehouse.storage_location), 0.)
            self.assertEqual(quantity(laundry), 10.)
            with self.assertRaises(MaintenanceError):
                Batch.delete([batch])
            line, = batch.lines
            line.qty_returned = 9.
            line.save()
            Batch.receive([batch])
            self.assertEqual(batch.state, 'received')
            self.assertEqual(quantity(laundry), 1.)
            self.assertEqual(
                quantity(hotel.warehouse.storage_location), 9.)
            self.assertEqual(batch.lines[0].lost, 1.)

    @with_transaction()
    def test_laundry_returned_more_than_sent(self):
        "Test the laundry cannot return more than it received"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Batch = pool.get('hotel.laundry.batch')
            Location = pool.get('stock.location')
            Party = pool.get('party.party')
            hotel, room = self._setup(company)
            today = pool.get('ir.date').today()
            product = self._linen()
            laundry, = Location.create([{
                        'name': "Laundry", 'type': 'storage'}])
            hotel.laundry_location = laundry
            hotel.save()
            supplier, = Party.create([{'name': "Laundry"}])
            batch, = Batch.create([{
                        'hotel': hotel.id, 'supplier': supplier.id,
                        'date': today,
                        'lines': [('create', [{
                                        'product': product.id,
                                        'unit': product.default_uom.id,
                                        'qty_sent': 2.,
                                        'qty_returned': 3.}])]}])
            Batch.send([batch])
            with self.assertRaises(MaintenanceError):
                Batch.receive([batch])
