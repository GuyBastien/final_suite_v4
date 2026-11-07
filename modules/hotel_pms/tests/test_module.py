# This file is part of the Tryton hotel_pms module.
import datetime
import io
import zipfile
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.modules.account.tests import create_chart, get_fiscalyear
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from .. import report as hotel_report
from .fixture import PmsFixture
from ..exceptions import ReservationError

START = datetime.datetime(2030, 1, 7, 14, 0)    # Monday
END = datetime.datetime(2030, 1, 9, 11, 0)      # 2 nights


class HotelPmsTestCase(PmsFixture, CompanyTestMixin, ModuleTestCase):
    "Test hotel_pms module"
    module = 'hotel_pms'

    @with_transaction()
    def test_availability(self):
        "Test overlapping reservations block a room"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            first = self._reservation(
                company, hotel, party, room_type, room1)
            self.assertTrue(first.number)
            self.assertTrue(room1.is_available(START, END))
            Reservation.confirm([first])
            self.assertEqual(first.state, 'confirmed')
            self.assertFalse(room1.is_available(START, END))
            self.assertTrue(room1.is_available(START, END, exclude=first))
            # Back-to-back stays do not overlap
            self.assertTrue(room1.is_available(
                    END, END + datetime.timedelta(days=1)))
            self.assertEqual(
                room_type.get_available_rooms(START, END), [room2])
            second = self._reservation(
                company, hotel, party, room_type, room1)
            with self.assertRaises(ReservationError):
                Reservation.confirm([second])
            # Cancelled reservations release the room
            Reservation.cancel([first])
            self.assertTrue(room1.is_available(START, END))

    @with_transaction()
    def test_unavailable_room_state(self):
        "Test rooms in maintenance cannot be booked"
        company = create_company()
        with set_company(company):
            Room = Pool().get('hotel.room')
            hotel, room_type, (room1, room2), party = self._setup(company)
            Room.write([room1], {'state': 'maintenance'})
            self.assertFalse(room1.is_available(START, END))

    @with_transaction()
    def test_workflow_and_room_states(self):
        "Test confirm, check-in and check-out update the rooms"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            line, = reservation.lines
            self.assertEqual(line.billed_as, 'nightly')
            self.assertEqual(line.quantity, Decimal(2))
            self.assertEqual(line.amount, Decimal('200.00'))
            self.assertEqual(reservation.total_amount, Decimal('200.00'))
            Reservation.check_in([reservation])
            self.assertEqual(reservation.state, 'checked_in')
            self.assertEqual(room1.state, 'occupied')
            self.assertTrue(reservation.actual_start)
            Reservation.check_out([reservation])
            self.assertEqual(reservation.state, 'checked_out')
            self.assertEqual(room1.state, 'dirty')

    @with_transaction()
    def test_check_in_room_not_ready(self):
        "Test check-in is refused when the room is not available"
        company = create_company()
        with set_company(company):
            Pool_ = Pool()
            Reservation = Pool_.get('hotel.reservation')
            Room = Pool_.get('hotel.room')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Room.write([room1], {'state': 'dirty'})
            with self.assertRaises(ReservationError):
                Reservation.check_in([reservation])

    @with_transaction()
    def test_hourly_stay(self):
        "Test short stays are billed by the hour"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1,
                start=START, end=START + datetime.timedelta(hours=3))
            Reservation.confirm([reservation])
            line, = reservation.lines
            self.assertEqual(line.billed_as, 'hourly')
            self.assertEqual(line.quantity, Decimal(3))
            self.assertEqual(line.amount, Decimal('60.00'))

    @with_transaction()
    def test_confirm_validations(self):
        "Test a reservation without lines is refused"
        company = create_company()
        with set_company(company):
            Pool_ = Pool()
            Reservation = Pool_.get('hotel.reservation')
            hotel, room_type, rooms, party = self._setup(company)
            empty, = Reservation.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'party': party.id, 'start': START, 'end': END}])
            with self.assertRaises(ReservationError):
                Reservation.confirm([empty])

    @with_transaction()
    def test_consumption_charged_once(self):
        "Test billable consumptions become charges at check-out, once"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Consumption = pool.get('hotel.consumption')
            Move = pool.get('stock.move')
            Location = pool.get('stock.location')
            today = pool.get('ir.date').today()
            hotel, room_type, (room1, room2), party = self._setup(company)

            Template = pool.get('product.template')
            Uom = pool.get('product.uom')
            unit, = Uom.search([('name', '=', "Unit")])
            template, = Template.create([{
                        'name': "Water", 'type': 'goods',
                        'account_category': self._category(),
                        'default_uom': unit.id, 'list_price': Decimal('3'),
                        'salable': True, 'sale_uom': unit.id,
                        'hotel_article': True, 'hotel_category': 'minibar',
                        'hotel_billable': True,
                        'products': [('create', [{
                                        'cost_price': Decimal('1')}])],
                        }])
            product = template.products[0]
            supplier, = Location.create([{'name': "S", 'type': 'supplier'}])
            receipt, = Move.create([{
                        'product': product.id, 'unit': unit.id,
                        'quantity': 10.,
                        'from_location': supplier.id,
                        'to_location': hotel.warehouse.storage_location.id,
                        'company': company.id,
                        'currency': company.currency.id,
                        'unit_price': Decimal('1'),
                        'effective_date': today}])
            with Transaction().set_context(_skip_warnings=True):
                Move.do([receipt])

            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Reservation.check_in([reservation])

            consumption, = Consumption.create([{
                        'company': company.id, 'hotel': hotel.id,
                        'kind': 'consumption', 'room': room1.id,
                        'date': today,
                        'lines': [('create', [{
                                        'product': product.id,
                                        'quantity': 2., 'unit': unit.id,
                                        'billable': True,
                                        'unit_price': Decimal('3')}])]}])
            # Auto-linked to the stay in house
            self.assertEqual(consumption.reservation, reservation)
            Consumption.process([consumption])

            Reservation.check_out([reservation])
            charge, = reservation.charges
            self.assertEqual(charge.product, product)
            self.assertEqual(charge.quantity, 2.)
            self.assertEqual(charge.amount, Decimal('6.00'))
            self.assertEqual(reservation.total_amount, Decimal('206.00'))
            # A second import does not duplicate charges
            reservation.import_consumptions()
            self.assertEqual(
                len(Reservation(reservation.id).charges), 1)

    @with_transaction()
    def test_create_sale(self):
        "Test the sale generated from a reservation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Reservation.create_sale([reservation])
            sale = reservation.sale
            self.assertEqual(sale.party, party)
            self.assertEqual(sale.origin, reservation)
            line, = sale.lines
            self.assertEqual(line.product, room_type.night_product)
            self.assertEqual(line.quantity, 2.)
            self.assertEqual(line.unit_price, Decimal('100.0000'))
            self.assertEqual(sale.untaxed_amount, Decimal('200.00'))
            with self.assertRaises(ReservationError):
                Reservation.create_sale([reservation])

    @with_transaction()
    def test_delete_rules(self):
        "Test only draft/cancelled reservations can be deleted"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            with self.assertRaises(ReservationError):
                Reservation.delete([reservation])
            Reservation.cancel([reservation])
            Reservation.delete([reservation])

    def _stays(self, company):
        "Two billed stays: 2 nights in room 101, hourly in room 102"
        Reservation = Pool().get('hotel.reservation')
        hotel, room_type, (room1, room2), party = self._setup(company)
        first = self._reservation(company, hotel, party, room_type, room1)
        second = self._reservation(
            company, hotel, party, room_type, room2,
            start=START, end=START + datetime.timedelta(hours=3))
        Reservation.confirm([first, second])
        return hotel, room_type, first, second

    @with_transaction()
    def test_report_occupancy(self):
        "Test daily occupancy, ADR and RevPAR"
        company = create_company()
        with set_company(company):
            hotel, room_type, first, second = self._stays(company)
            day = START.date()
            rows = hotel_report.occupancy_rows(
                hotel, day, day + datetime.timedelta(days=2))
            self.assertEqual([r.occupied for r in rows], [2, 1, 0])
            self.assertEqual([r.rooms for r in rows], [2, 2, 2])
            self.assertEqual(rows[0].rate, Decimal(100))
            # day 1: 100 (night) + 60 (3h); day 2: 100
            self.assertEqual(rows[0].revenue, Decimal('160'))
            self.assertEqual(rows[0].adr, Decimal('80'))
            self.assertEqual(rows[0].revpar, Decimal('80'))
            total = hotel_report.occupancy_total(rows)
            self.assertEqual(total.occupied, 3)
            self.assertEqual(total.rooms, 6)
            self.assertEqual(total.revenue, Decimal('260'))

    @with_transaction()
    def test_report_revenue_and_movements(self):
        "Test revenue by room type, arrivals and departures"
        company = create_company()
        with set_company(company):
            hotel, room_type, first, second = self._stays(company)
            day = START.date()
            rows = hotel_report.revenue_rows(hotel, day, day)
            row, = rows
            self.assertEqual(row.amount, Decimal('160'))
            arrivals = hotel_report.movement_rows(hotel, day, day, 'start')
            self.assertEqual(len(arrivals), 2)
            # Nobody is in house yet: no departure
            self.assertEqual(
                hotel_report.movement_rows(
                    hotel, day, day + datetime.timedelta(days=3), 'end'), [])

    @with_transaction()
    def test_report_rendering(self):
        "Test the report templates render"
        company = create_company()
        with set_company(company):
            pool = Pool()
            hotel, room_type, first, second = self._stays(company)
            day = START.date()
            data = {'hotel': hotel.id, 'start_date': day,
                'end_date': day + datetime.timedelta(days=2)}
            for name in ['arrivals', 'departures', 'occupancy', 'revenue']:
                Report = pool.get('hotel_pms.report.' + name, type='report')
                ext, content, _, _ = Report.execute([], data)
                self.assertEqual(ext, 'odt')
                self.assertTrue(content)
                with zipfile.ZipFile(io.BytesIO(content)) as odt:
                    text = odt.read('content.xml').decode()
                if name == 'arrivals':
                    self.assertIn(first.number, text)
                    self.assertIn("Guest", text)
                if name == 'occupancy':
                    self.assertIn("Grand Hotel", text)
            Folio = pool.get('hotel.reservation.folio', type='report')
            ext, content, _, _ = Folio.execute([first.id], {})
            with zipfile.ZipFile(io.BytesIO(content)) as odt:
                text = odt.read('content.xml').decode()
            self.assertIn(first.number, text)
            self.assertIn("200", text)

    @with_transaction()
    def test_order_points_and_requests(self):
        "Test order points from room standards and purchase requests"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Hotel = pool.get('hotel.hotel')
            Article = pool.get('hotel.room.type.article')
            OrderPoint = pool.get('stock.order_point')
            Request = pool.get('purchase.request')
            Template = pool.get('product.template')
            Uom = pool.get('product.uom')
            hotel, room_type, rooms, party = self._setup(company)
            unit, = Uom.search([('name', '=', "Unit")])
            template, = Template.create([{
                        'name': "Soap", 'type': 'goods',
                        'default_uom': unit.id, 'list_price': Decimal('1'),
                        'purchasable': True, 'purchase_uom': unit.id,
                        'account_category': self._category(),
                        'hotel_article': True, 'hotel_category': 'amenity',
                        'products': [('create', [{
                                        'cost_price': Decimal('1')}])],
                        }])
            product = template.products[0]
            Article.create([{
                        'room_type': room_type.id, 'product': product.id,
                        'quantity': 2., 'unit': unit.id}])
            hotel = Hotel(hotel.id)
            self.assertEqual(hotel.reorder_ratio, 0.5)
            Hotel.update_order_points([hotel])
            point, = OrderPoint.search([('product', '=', product.id)])
            self.assertEqual(point.type, 'purchase')
            self.assertEqual(point.warehouse_location, hotel.warehouse)
            # 2 rooms x 2 soaps
            self.assertEqual(point.target_quantity, 4.)
            self.assertEqual(point.min_quantity, 2.)
            # Running again updates instead of duplicating
            Hotel.write([hotel], {'reorder_ratio': 0.25})
            Hotel.update_order_points([hotel])
            point, = OrderPoint.search([('product', '=', product.id)])
            self.assertEqual(point.min_quantity, 1.)
            # Empty stock: a purchase request for the target is raised
            Request.generate_requests(warehouses=[hotel.warehouse])
            request, = Request.search([('product', '=', product.id)])
            self.assertEqual(request.quantity, 4.)

    @with_transaction()
    def test_invoice_from_reservation(self):
        "Test the sale of a stay is confirmed, invoiced and posted"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Sale = pool.get('sale.sale')
            Invoice = pool.get('account.invoice')
            PaymentTerm = pool.get('account.invoice.payment_term')
            Account = pool.get('account.account')
            Party = pool.get('party.party')
            hotel, room_type, (room1, room2), party = self._setup(company)
            # accounting setup (chart is created by _setup) + fiscal year
            fiscalyear = get_fiscalyear(company)
            fiscalyear.save()
            fiscalyear.create_period([fiscalyear])
            sequence_type, = pool.get('ir.sequence.type').search([
                    ('name', '=', "Invoice")], limit=1)
            sequence, = pool.get('ir.sequence.strict').create([{
                        'name': "Invoice", 'company': company.id,
                        'sequence_type': sequence_type.id}])
            pool.get('account.fiscalyear.invoice_sequence').create([{
                        'fiscalyear': fiscalyear.id,
                        'company': company.id,
                        'out_invoice_sequence': sequence.id,
                        'in_invoice_sequence': sequence.id,
                        'out_credit_note_sequence': sequence.id,
                        'in_credit_note_sequence': sequence.id}])
            receivable, = Account.search([
                    ('type.receivable', '=', True),
                    ('company', '=', company.id)], limit=1)
            payable, = Account.search([
                    ('type.payable', '=', True),
                    ('company', '=', company.id)], limit=1)
            party = Party(party.id)
            party.account_receivable = receivable
            party.account_payable = payable
            party.save()
            term, = PaymentTerm.create([{
                        'name': "Immediate",
                        'lines': [('create', [{'type': 'remainder'}])]}])
            party.customer_payment_term = term
            party.save()
            pool.get('party.address').create([{'party': party.id}])

            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Reservation.create_sale([reservation])
            sale = reservation.sale
            Sale.quote([sale])
            Sale.confirm([sale])
            Sale.process([sale])
            sale = Sale(sale.id)
            invoice, = sale.invoices
            self.assertEqual(Reservation(reservation.id).sale_state, 'processing')
            self.assertEqual(invoice.party, party)
            self.assertEqual(invoice.untaxed_amount, Decimal('200.00'))
            Invoice.validate_invoice([invoice])
            Invoice.post([invoice])
            self.assertEqual(invoice.state, 'posted')
            # Tryton refreshes the sale when its invoices change (trigger)
            Sale.process([Sale(sale.id)])
            self.assertEqual(
                Reservation(reservation.id).invoice_state, 'awaiting payment')
            self.assertEqual(invoice.total_amount, Decimal('200.00'))
    @with_transaction()
    def test_discount(self):
        "Test the room discount is applied to amounts and the sale"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            line, = reservation.lines
            line.discount_rate = Decimal('0.10')
            line.save()
            Reservation.confirm([reservation])
            line, = Reservation(reservation.id).lines
            self.assertEqual(line.unit_price, Decimal('100.0000'))
            self.assertEqual(line.amount, Decimal('180.00'))
            self.assertEqual(
                Reservation(reservation.id).total_amount, Decimal('180.00'))
            Reservation.create_sale([reservation])
            sale_line, = reservation.sale.lines
            self.assertEqual(sale_line.discount_rate, Decimal('0.1000'))
            self.assertEqual(sale_line.unit_price, Decimal('90.0000'))
            self.assertEqual(reservation.sale.untaxed_amount, Decimal('180.00'))
    @with_transaction()
    def test_long_stay_subscription(self):
        "Test a long stay is billed by a subscription at the agreed price"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            RuleSet = pool.get('sale.subscription.recurrence.rule.set')
            Service = pool.get('sale.subscription.service')
            Subscription = pool.get('sale.subscription')
            hotel, room_type, (room1, room2), party = self._setup(company)
            pool.get('party.address').create([{'party': party.id}])
            monthly, daily = RuleSet.create([
                    {'name': "Monthly", 'rules': [('create', [
                                    {'freq': 'monthly', 'interval': 1}])]},
                    {'name': "Daily", 'rules': [('create', [
                                    {'freq': 'daily', 'interval': 1}])]},
                    ])
            reservation = self._reservation(
                company, hotel, party, room_type, room1,
                start=START, end=START + datetime.timedelta(days=30))
            line, = reservation.lines
            line.discount_rate = Decimal('0.20')
            line.save()
            Reservation.confirm([reservation])
            # No recurrence / no service: refused
            with self.assertRaises(ReservationError):
                Reservation.create_subscription([reservation])
            Reservation.write([reservation], {'invoice_recurrence': monthly.id})
            with self.assertRaises(ReservationError):
                Reservation.create_subscription([reservation])
            Service.create([{
                        'product': room_type.night_product.id,
                        'consumption_recurrence': daily.id}])
            Reservation.create_subscription([reservation])
            subscription = Reservation(reservation.id).subscription
            self.assertEqual(subscription.party, party)
            self.assertEqual(subscription.start_date, START.date())
            # 30 nights: the departure day is not billed
            self.assertEqual(
                subscription.end_date, START.date() + datetime.timedelta(days=29))
            sub_line, = subscription.lines
            self.assertEqual(sub_line.unit_price, Decimal('80.0000'))
            Subscription.quote([subscription])
            Subscription.run([subscription])
            self.assertEqual(Subscription(subscription.id).state, 'running')
            # One night is consumed per day at the agreed price
            SubLine = pool.get('sale.subscription.line')
            SubLine.generate_consumption(
                date=START.date() + datetime.timedelta(days=2))
            consumptions = pool.get(
                'sale.subscription.line.consumption').search([])
            self.assertEqual(len(consumptions), 3)
            self.assertEqual(sum(c.quantity for c in consumptions), 3.)
            # Rooms are billed by the subscription: nothing else to sell
            with self.assertRaises(ReservationError):
                Reservation.create_sale([reservation])
            with self.assertRaises(ReservationError):
                Reservation.create_subscription([reservation])
    @with_transaction()
    def test_pos_room_charge(self):
        "Test POS sales charged to a room are settled by the stay sale"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            Account = pool.get('account.account')
            Category = pool.get('product.category')
            Method = pool.get('sale.point.payment.method')
            POS = pool.get('sale.point')
            POSSale = pool.get('sale.point.sale')
            Payment = pool.get('sale.point.payment')
            Hotel = pool.get('hotel.hotel')
            hotel, room_type, (room1, room2), party = self._setup(company)
            pool.get('party.address').create([{'party': party.id}])
            fiscalyear = get_fiscalyear(company)
            fiscalyear.save()
            fiscalyear.create_period([fiscalyear])
            revenue, = Account.search([
                    ('type.revenue', '=', True),
                    ('company', '=', company.id)], limit=1)
            expense, = Account.search([
                    ('type.expense', '=', True),
                    ('company', '=', company.id)], limit=1)
            clearing, = Account.create([{
                        'name': "Room charges clearing",
                        'parent': revenue.parent.id,
                        'type': revenue.type.id,
                        'company': company.id}])
            category, = Category.create([{
                        'name': "Clearing", 'accounting': True,
                        'account_revenue': clearing.id,
                        'account_expense': expense.id}])
            Hotel.write([hotel], {
                    'room_charge_product': self._service(
                        "Room charges", Decimal(0),
                        category=category.id).id})
            method, = Method.create([{
                        'name': "Room", 'company': company.id,
                        'account': clearing.id, 'room_charge': True}])
            sequence_type_id = pool.get('ir.model.data').get_id(
                'sale_point', 'sequence_type_sale')
            sequence, = pool.get('ir.sequence.strict').create([{
                        'name': "POS", 'company': company.id,
                        'sequence_type': sequence_type_id}])
            journal, = pool.get('account.journal').search(
                [('type', '=', 'revenue')], limit=1)
            point, = POS.create([{
                        'name': "Bar", 'company': company.id,
                        'sequence': sequence.id,
                        'storage_location': hotel.warehouse.storage_location.id,
                        'customer_location': hotel.consumption_location.id,
                        'journal': journal.id}])
            drink = self._service("Cocktail", Decimal('12'))
            today = pool.get('ir.date').today()

            def pos_sale(reservation):
                return POSSale.create([{
                            'company': company.id, 'point': point.id,
                            'date': today,
                            'reservation': (
                                reservation.id if reservation else None),
                            'lines': [('create', [{
                                            'product': drink.id,
                                            'quantity': 2.,
                                            'unit_list_price': Decimal('12'),
                                            'unit_gross_price': Decimal('12'),
                                            }])]}])[0]

            # Room charge without a checked-in reservation is refused
            sale = pos_sale(None)
            with self.assertRaises(ReservationError):
                Payment.create([{'sale': sale.id, 'method': method.id,
                            'amount': Decimal('24')}])

            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.confirm([reservation])
            Reservation.check_in([reservation])
            sale = pos_sale(reservation)
            Payment.create([{'sale': sale.id, 'method': method.id,
                        'amount': Decimal('24')}])
            # Not counted while the POS sale is open
            self.assertEqual(
                Reservation(reservation.id).pos_amount, Decimal(0))
            POSSale.process([sale])
            POSSale.post([sale])
            self.assertEqual(sale.state, 'posted')
            reservation = Reservation(reservation.id)
            self.assertEqual(reservation.pos_amount, Decimal('24.00'))

            Reservation.check_out([reservation])
            Reservation.create_sale([reservation])
            lines = reservation.sale.lines
            self.assertEqual(len(lines), 2)
            self.assertEqual(
                sorted(l.amount for l in lines),
                [Decimal('24.00'), Decimal('200.00')])
            self.assertEqual(
                Reservation(reservation.id).pos_amount, Decimal(0))

    # Dashboard, notifications and documentation

    def _stay(self, company, hotel, party, room_type, room, start, end,
            state=None):
        Reservation = Pool().get('hotel.reservation')
        reservation = self._reservation(
            company, hotel, party, room_type, room, start, end)
        if state in {'confirmed', 'checked_in'}:
            Reservation.confirm([reservation])
        if state == 'checked_in':
            Reservation.check_in([reservation])
        return reservation

    def _keys(self, hotel, kind=None):
        Notification = Pool().get('hotel.notification')
        domain = [('hotel', '=', hotel.id), ('state', '=', 'open')]
        if kind:
            domain.append(('kind', '=', kind))
        return {n.key.split(':')[0] for n in Notification.search(domain)}

    @with_transaction()
    def test_notification_rules(self):
        "Test each kind of notification is raised by its rule"
        company = create_company()
        with set_company(company):
            Notification = Pool().get('hotel.notification')
            hotel, room_type, (room1, room2), party = self._setup(company)
            now = datetime.datetime.now().replace(microsecond=0)
            day = datetime.timedelta(days=1)
            hour = datetime.timedelta(hours=1)
            # arrival soon -> reminder
            self._stay(company, hotel, party, room_type, room1,
                now + 2 * hour, now + 2 * day, 'confirmed')
            # guest never came -> alert
            self._stay(company, hotel, party, room_type, room2,
                now - 10 * hour, now + 2 * day, 'confirmed')
            Notification.generate([hotel])
            self.assertIn('arrival', self._keys(hotel, 'reminder'))
            self.assertIn('noshow', self._keys(hotel, 'alert'))

    @with_transaction()
    def test_notification_idempotent_and_resolved(self):
        "Test generation is idempotent, resolves and respects dismissal"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Notification = pool.get('hotel.notification')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            now = datetime.datetime.now().replace(microsecond=0)
            hour = datetime.timedelta(hours=1)
            reservation = self._stay(company, hotel, party, room_type, room1,
                now - 10 * hour, now + datetime.timedelta(days=2),
                'confirmed')
            first = Notification.generate([hotel])
            self.assertTrue(first)
            self.assertEqual(Notification.generate([hotel]), [])
            noshow, = Notification.search([
                    ('hotel', '=', hotel.id), ('key', '=', 'noshow:%s'
                        % reservation.id)])
            self.assertEqual(noshow.kind, 'alert')
            self.assertEqual(noshow.state, 'open')
            Notification.dismiss([noshow])
            Notification.generate([hotel])
            self.assertEqual(Notification(noshow.id).state, 'dismissed')
            # condition disappears: the open ones are marked done
            Reservation.cancel([reservation])
            Notification.generate([hotel])
            self.assertEqual(
                Notification.search_count([
                        ('hotel', '=', hotel.id), ('state', '=', 'open'),
                        ('key', 'like', 'noshow:%')]), 0)

    @with_transaction()
    def test_notification_overdue_and_not_ready(self):
        "Test overdue check-out alert and room not ready warning"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Notification = pool.get('hotel.notification')
            hotel, room_type, (room1, room2), party = self._setup(company)
            now = datetime.datetime.now().replace(microsecond=0)
            hour = datetime.timedelta(hours=1)
            self._stay(company, hotel, party, room_type, room1,
                now - datetime.timedelta(days=2), now - hour, 'checked_in')
            room2.state = 'dirty'
            room2.save()
            self._stay(company, hotel, party, room_type, room2,
                now + 2 * hour, now + datetime.timedelta(days=2),
                'confirmed')
            Notification.generate([hotel])
            self.assertIn('overdue', self._keys(hotel, 'alert'))
            self.assertIn('notready', self._keys(hotel, 'warning'))

    @with_transaction()
    def test_notification_manual(self):
        "Test manual reminders can be created and handled"
        company = create_company()
        with set_company(company):
            Notification = Pool().get('hotel.notification')
            hotel, *_ = self._setup(company)
            note, = Notification.create([{
                        'hotel': hotel.id, 'kind': 'reminder',
                        'title': "Call supplier", 'message': "Tomorrow",
                        'date': datetime.datetime.now()}])
            self.assertEqual(note.state, 'open')
            self.assertFalse(note.automatic)
            Notification.done([note])
            self.assertEqual(note.state, 'done')
            Notification.reopen([note])
            self.assertEqual(note.state, 'open')

    @with_transaction()
    def test_dashboard(self):
        "Test the dashboard figures"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Dashboard = pool.get('hotel.dashboard')
            Notification = pool.get('hotel.notification')
            hotel, room_type, (room1, room2), party = self._setup(company)
            now = datetime.datetime.now().replace(microsecond=0)
            self._stay(company, hotel, party, room_type, room1,
                now - datetime.timedelta(hours=1),
                now + datetime.timedelta(days=2), 'checked_in')
            dashboard, = Dashboard.search([('hotel', '=', hotel.id)])
            self.assertEqual(dashboard.rec_name, hotel.name)
            self.assertEqual(dashboard.rooms_total, 2)
            self.assertEqual(dashboard.rooms_occupied, 1)
            self.assertEqual(dashboard.in_house, 1)
            self.assertEqual(dashboard.occupancy_rate, Decimal('50.0'))
            self.assertEqual(len(dashboard.in_house_reservations), 1)
            Notification.generate([hotel])
            dashboard = Dashboard(dashboard.id)
            self.assertEqual(
                dashboard.open_alerts + dashboard.open_warnings
                + dashboard.open_reminders,
                len([n for n in dashboard.notifications
                        if n.kind != 'info']))
            Dashboard.refresh([dashboard])

    @with_transaction()
    def test_documentation_and_cron(self):
        "Test documentation records and the notification cron"
        pool = Pool()
        Documentation = pool.get('hotel.documentation')
        Cron = pool.get('ir.cron')
        ModelData = pool.get('ir.model.data')
        self.assertGreaterEqual(Documentation.search_count([]), 11)
        for doc in Documentation.search([]):
            self.assertTrue(doc.title)
            self.assertTrue(doc.content)
        cron = Cron(ModelData.get_id('hotel_pms', 'cron_notifications'))
        self.assertEqual(cron.method, 'hotel.notification|generate')

    @with_transaction()
    def test_on_change_notify(self):
        "Test reservation warnings"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            self.assertTrue(hasattr(Reservation, 'on_change_notify'))
            reservation.on_change_notify()


del ModuleTestCase
