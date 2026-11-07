# This file is part of the Tryton hotel_groups module.
import datetime
from decimal import Decimal

from trytond.modules.account.tests import create_chart
from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import EventError, GroupError

START = datetime.date(2030, 3, 4)
END = datetime.date(2030, 3, 7)    # 3 nights


class HotelGroupsTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_groups module"
    module = 'hotel_groups'

    def _service(self, name, price):
        pool = Pool()
        Template = pool.get('product.template')
        Category = pool.get('product.category')
        Account = pool.get('account.account')
        Uom = pool.get('product.uom')
        create_chart(pool.get('company.company')(
                Transaction().context['company']))
        revenue, = Account.search([('type.revenue', '=', True)], limit=1)
        expense, = Account.search([('type.expense', '=', True)], limit=1)
        category, = Category.create([{
                    'name': "Hotel", 'accounting': True,
                    'account_revenue': revenue.id,
                    'account_expense': expense.id}])
        unit, = Uom.search([('name', '=', "Unit")])
        template, = Template.create([{
                    'name': name, 'type': 'service',
                    'account_category': category.id,
                    'default_uom': unit.id, 'sale_uom': unit.id,
                    'salable': True, 'list_price': price,
                    'products': [('create', [{}])],
                    }])
        return template.products[0]

    def _setup(self, company, rooms=('101', '102', '103')):
        pool = Pool()
        Room = pool.get('hotel.room')
        Party = pool.get('party.party')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        Room.create([{'hotel': hotel.id, 'name': n, 'type': room_type.id}
                for n in rooms])
        party, = Party.create([{'name': "Acme Corp"}])
        return hotel, room_type, party

    def _group(self, company, hotel, room_type, party, quantity=2, **values):
        Group = Pool().get('hotel.group')
        values.update({
                'name': "Congress", 'company': company.id,
                'hotel': hotel.id, 'party': party.id,
                'start_date': START, 'end_date': END,
                'blocks': [('create', [{
                                'room_type': room_type.id,
                                'quantity': quantity}])],
                })
        return Group.create([values])[0]

    @with_transaction()
    def test_group_number_and_validation(self):
        "Test sequence, dates, cut-off and block quantity validation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Group = pool.get('hotel.group')
            Block = pool.get('hotel.group.block')
            hotel, room_type, party = self._setup(company)
            group = self._group(company, hotel, room_type, party)
            self.assertTrue(group.number)
            self.assertEqual(group.state, 'draft')
            self.assertEqual(group.nights, 3)
            with self.assertRaises(GroupError):
                Group.write([group], {'cutoff_date': END})
            Group.write([group], {'cutoff_date': START})
            with self.assertRaises(GroupError):
                Block.write(list(group.blocks), {'quantity': 4})

    @with_transaction()
    def test_generate_reservations_and_pickup(self):
        "Test generated reservations, pickup and remaining rooms"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Group = pool.get('hotel.group')
            hotel, room_type, party = self._setup(company)
            group = self._group(company, hotel, room_type, party)
            block, = group.blocks
            self.assertEqual((block.pickup, block.remaining), (0, 2))
            reservations = group.generate()
            self.assertEqual(len(reservations), 2)
            for reservation in reservations:
                self.assertEqual(reservation.group, group)
                self.assertEqual(reservation.party, party)
                self.assertEqual(reservation.start.date(), START)
                self.assertEqual(reservation.end.date(), END)
                self.assertEqual(reservation.start.time(), hotel.check_in_time)
            block = Group(group.id).blocks[0]
            self.assertEqual((block.pickup, block.remaining), (2, 0))
            # Nothing more to generate
            self.assertEqual(group.generate(), [])
            # Rooming list search by guest
            Line = pool.get('hotel.reservation.line')
            self.assertEqual(
                len(Line.search([('guest', '=', party.id)])), 2)

    @with_transaction()
    def test_negotiated_price(self):
        "Test the negotiated price is kept on the generated lines"
        company = create_company()
        with set_company(company):
            hotel, room_type, party = self._setup(company)
            group = self._group(company, hotel, room_type, party, quantity=1)
            block, = group.blocks
            block.nightly_price = Decimal('80')
            block.save()
            reservation, = group.generate()
            line, = reservation.lines
            self.assertEqual(line.unit_price, Decimal('80'))
            self.assertEqual(line.quantity, Decimal(3))
            self.assertEqual(reservation.total_amount, Decimal('240.00'))

    @with_transaction()
    def test_release_after_cutoff(self):
        "Test unused rooms are released after the cut-off date"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Group = pool.get('hotel.group')
            Date = pool.get('ir.date')
            hotel, room_type, party = self._setup(company)
            group = self._group(company, hotel, room_type, party,
                cutoff_date=START)
            Group.definite([group])
            with self.assertRaises(GroupError):
                # The cut-off date is in the future
                Group.release([group])
            group.cutoff_date = Date.today() - datetime.timedelta(days=1)
            Group.write([group], {'start_date': Date.today()
                    + datetime.timedelta(days=5),
                    'end_date': Date.today() + datetime.timedelta(days=8),
                    'cutoff_date': Date.today() - datetime.timedelta(days=1)})
            group = Group(group.id)
            # One room picked up, one released
            Group.write([group], {'state': 'draft'})
            block, = group.blocks
            reservation, _ = group.generate()
            reservation.group = group
            Group.write([group], {'state': 'definite'})
            # Cancel one reservation to lower the pickup to one
            pool.get('hotel.reservation').cancel([_])
            Group.release([group])
            block, = Group(group.id).blocks
            self.assertEqual(block.quantity, 1)

    @with_transaction()
    def test_cancel_group(self):
        "Test cancelling a group cancels its open reservations"
        company = create_company()
        with set_company(company):
            Group = Pool().get('hotel.group')
            hotel, room_type, party = self._setup(company)
            group = self._group(company, hotel, room_type, party)
            reservations = group.generate()
            Group.cancel([group])
            self.assertEqual(Group(group.id).state, 'cancelled')
            self.assertEqual(
                {r.state for r in reservations
                    for r in [type(r)(r.id)]}, {'cancelled'})
            with self.assertRaises(GroupError):
                Group.delete([Group(group.id)])

    @with_transaction()
    def test_definite_needs_blocks(self):
        "Test a group without block cannot become definite"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Group = pool.get('hotel.group')
            hotel, room_type, party = self._setup(company)
            group, = Group.create([{
                        'name': "Empty", 'company': company.id,
                        'hotel': hotel.id, 'party': party.id,
                        'start_date': START, 'end_date': END}])
            with self.assertRaises(GroupError):
                Group.definite([group])

    def _space(self, hotel, **values):
        Space = Pool().get('hotel.event.space')
        values.update(hotel=hotel.id, name="Hall", capacity=50)
        return Space.create([values])[0]

    @with_transaction()
    def test_event_overlap_and_capacity(self):
        "Test events cannot overlap in a space nor exceed its capacity"
        company = create_company()
        with set_company(company):
            Event = Pool().get('hotel.event')
            hotel, room_type, party = self._setup(company)
            space = self._space(hotel)
            event, = Event.create([{
                        'name': "Opening", 'space': space.id,
                        'start': datetime.datetime(2030, 3, 4, 9),
                        'end': datetime.datetime(2030, 3, 4, 12),
                        'attendees': 40}])
            self.assertEqual(event.date, datetime.date(2030, 3, 4))
            Event.create([{
                        'name': "After", 'space': space.id,
                        'start': datetime.datetime(2030, 3, 4, 12),
                        'end': datetime.datetime(2030, 3, 4, 14)}])
            with self.assertRaises(EventError):
                Event.create([{
                            'name': "Clash", 'space': space.id,
                            'start': datetime.datetime(2030, 3, 4, 11),
                            'end': datetime.datetime(2030, 3, 4, 13)}])
            with self.assertRaises(EventError):
                Event.write([event], {'attendees': 51})
            # A cancelled event does not block the space
            Event.cancel([event])
            Event.create([{
                        'name': "Reuse", 'space': space.id,
                        'start': datetime.datetime(2030, 3, 4, 9),
                        'end': datetime.datetime(2030, 3, 4, 10)}])

    @with_transaction()
    def test_event_bill(self):
        "Test billing an event adds a charge once"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Event = pool.get('hotel.event')
            hotel, room_type, party = self._setup(company)
            product = self._service("Hall hour", Decimal('50'))
            space = self._space(hotel, product=product.id,
                hourly_price=Decimal('50'))
            group = self._group(company, hotel, room_type, party, quantity=1)
            reservation, = group.generate()
            event, = Event.create([{
                        'name': "Workshop", 'space': space.id,
                        'group': group.id,
                        'start': datetime.datetime(2030, 3, 5, 9),
                        'end': datetime.datetime(2030, 3, 5, 11, 30)}])
            with self.assertRaises(EventError):
                Event.bill([event])
            event.reservation = reservation
            event.save()
            Event.bill([event])
            event = Event(event.id)
            self.assertEqual(event.charge.quantity, 3.0)
            self.assertEqual(event.charge.unit_price, Decimal('50'))
            self.assertEqual(
                event.charge.reservation, reservation)
