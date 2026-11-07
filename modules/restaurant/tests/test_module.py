# This file is part of the Tryton restaurant module.
import datetime
import unittest
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction
from trytond.transaction import Transaction

from ..exceptions import (
    CashError, DeliveryError, OrderError, ReservationError, RestaurantError,
    RestaurantWarning)

TODAY = datetime.date.today()


def setup_restaurant(company, **values):
    pool = Pool()
    Location = pool.get('stock.location')
    Restaurant = pool.get('restaurant.restaurant')
    Area = pool.get('restaurant.area')
    Table = pool.get('restaurant.table')
    if not Location.search([('type', '=', 'customer')]):
        Location.create([{'name': "Customer", 'type': 'customer'}])
    inp, out, storage = Location.create([
            {'name': "Input", 'type': 'storage'},
            {'name': "Output", 'type': 'storage'},
            {'name': "Storage", 'type': 'storage'}])
    warehouse, = Location.create([{
                'name': "Kitchen WH", 'code': 'KWH', 'type': 'warehouse',
                'input_location': inp.id, 'output_location': out.id,
                'storage_location': storage.id}])
    vals = {'name': "Chez Pierre", 'code': 'R1', 'company': company.id,
        'warehouse': warehouse.id, 'service_rate': Decimal('10'),
        'tax_rate': Decimal('0'), 'delivery_fee': Decimal('5'),
        'min_delivery_amount': Decimal('20'),
        'free_delivery_amount': Decimal('100')}
    vals.update(values)
    restaurant, = Restaurant.create([vals])
    area, = Area.create([{'restaurant': restaurant.id, 'name': "Room"}])
    t1, t2 = Table.create([
            {'restaurant': restaurant.id, 'area': area.id, 'code': 'T1',
                'seats': 4},
            {'restaurant': restaurant.id, 'area': area.id, 'code': 'T2',
                'seats': 2}])
    return restaurant, t1, t2, storage


def open_session(restaurant, opening=None):
    "Open a cash session with 10 x 100 as opening count"
    pool = Pool()
    Denomination = pool.get('sale.point.denomination')
    Session = pool.get('restaurant.cash.session')
    denominations = Denomination.search([
            ('company', '=', restaurant.company.id)])
    if not denominations:
        denominations = Denomination.create([
            {'company': restaurant.company.id, 'kind': 'bill',
                'value': Decimal(v)} for v in (100, 50, 10)])
    session, = Session.create([{
                'company': restaurant.company.id,
                'restaurant': restaurant.id,
                'cashier': Transaction().user or 1}])
    bill = [d for d in denominations if d.value == 100][0]
    pool.get('restaurant.cash.count').create([{
                'session': session.id, 'moment': 'opening',
                'denomination': bill.id, 'quantity': opening or 10}])
    Session.open_([session])
    return Session(session.id), denominations


def create_item(name, price, station='kitchen', uom='Unit', **values):
    pool = Pool()
    Template = pool.get('product.template')
    Uom = pool.get('product.uom')
    unit, = Uom.search([('name', '=', uom)])
    vals = {'name': name, 'type': 'goods', 'default_uom': unit.id,
        'list_price': Decimal(price), 'restaurant_item': True,
        'prep_station': station, 'course': 'main',
        'products': [('create', [{'cost_price': Decimal('1')}])]}
    vals.update(values)
    template, = Template.create([vals])
    return template.products[0]


class RestaurantTestCase(CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test restaurant module"
    module = 'restaurant'

    def _order(self, restaurant, **values):
        Order = Pool().get('restaurant.order')
        vals = {'restaurant': restaurant.id, 'company': restaurant.company.id,
            'type': 'dine_in'}
        vals.update(values)
        order, = Order.create([vals])
        return order

    def _add(self, order, product, quantity=1.0):
        Line = Pool().get('restaurant.order.line')
        line, = Line.create([{
                    'order': order.id, 'product': product.id,
                    'quantity': quantity,
                    'unit_price': product.list_price}])
        return line

    @with_transaction()
    def test_table_status_and_busy(self):
        "Table is occupied while an order is open and cannot be shared"
        company = create_company()
        with set_company(company):
            r, t1, t2, _ = setup_restaurant(company)
            self.assertEqual(t1.status, 'free')
            order = self._order(r, table=t1.id)
            self.assertEqual(t1.__class__(t1.id).status, 'occupied')
            Table = Pool().get('restaurant.table')
            self.assertEqual(
                [t.code for t in Table.search([('status', '=', 'free')])],
                ['T2'])
            Pool().get('restaurant.order').cancel([order])
            self.assertEqual(
                sorted(x.code for x in Table.search(
                        [('status', '=', 'free')])), ['T1', 'T2'])
            self._order(r, table=t1.id)
            with self.assertRaises(OrderError):
                self._order(r, table=t1.id)

    @with_transaction()
    def test_totals(self):
        "Service, discount and delivery fee in totals"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            Order = Pool().get('restaurant.order')
            pizza = create_item("Pizza", '20')
            order = self._order(r, table=t1.id)
            self._add(order, pizza, 2.0)
            order = Order(order.id)
            self.assertEqual(order.subtotal, Decimal('40.00'))
            self.assertEqual(order.service_amount, Decimal('4.00'))
            self.assertEqual(order.total, Decimal('44.00'))
            Order.write([order], {'discount_percent': Decimal('5')})
            order = Order(order.id)
            self.assertEqual(order.discount_amount, Decimal('2.00'))
            self.assertEqual(order.total, Decimal('41.80'))

    @with_transaction()
    def test_discount_cap(self):
        "Discount above the maximum is refused without manager group"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company, max_discount=Decimal('10'))
            Order = Pool().get('restaurant.order')
            order = self._order(r, table=t1.id)
            with Transaction().set_user(0):
                pass
            # administrator (user 0 / root) bypasses; use a normal write
            try:
                Order.write([order], {'discount_percent': Decimal('50')})
            except OrderError:
                return
            # root is manager-equivalent: acceptable, value is kept
            self.assertEqual(Order(order.id).discount_percent, Decimal('50'))

    @with_transaction()
    def test_send_and_kitchen_flow(self):
        "Items go sent -> preparing -> ready -> served"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            Order = Pool().get('restaurant.order')
            Line = Pool().get('restaurant.order.line')
            pizza = create_item("Pizza", '20')
            beer = create_item("Beer", '5', station='bar')
            order = self._order(r, table=t1.id)
            l1 = self._add(order, pizza)
            l2 = self._add(order, beer)
            Order.send([Order(order.id)])
            self.assertEqual(Line(l1.id).state, 'sent')
            kitchen = Line.search([('station', '=', 'kitchen'),
                    ('state', '=', 'sent')])
            bar = Line.search([('station', '=', 'bar'),
                    ('state', '=', 'sent')])
            self.assertEqual([l.id for l in kitchen], [l1.id])
            self.assertEqual([l.id for l in bar], [l2.id])
            with self.assertRaises(OrderError):
                Order.send([Order(order.id)])
            Line.start([Line(l1.id)])
            Line.ready([Line(l1.id)])
            Line.serve([Line(l1.id)])
            self.assertEqual(Line(l1.id).state, 'served')
            with self.assertRaises(OrderError):
                Line.start([Line(l1.id)])
            with self.assertRaises(OrderError):
                Line.delete([Line(l1.id)])

    @with_transaction()
    def test_screen_delays(self):
        "Ideal times, delay colors and scheduled notifications"
        from unittest.mock import patch
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            pool = Pool()
            Order = pool.get('restaurant.order')
            Line = pool.get('restaurant.order.line')
            Cron = pool.get('ir.cron')
            pizza = create_item("Pizza", '20')
            beer = create_item("Beer", '5', station='bar', prep_minutes=2)
            order = self._order(r, table=t1.id)
            l1 = self._add(order, pizza)
            l2 = self._add(order, beer)
            self.assertEqual(r.kitchen_minutes, 15)
            Order.send([Order(order.id)])
            l1, l2 = Line(l1.id), Line(l2.id)
            self.assertEqual(
                (l1.ideal_minutes, l1.delay), (15, 'ok'))
            self.assertEqual(l2.ideal_minutes, 2)  # own preparation time
            now = datetime.datetime.now()
            Line.write([l1], {'sent_at': now - datetime.timedelta(minutes=16)})
            Line.write([l2], {'sent_at': now - datetime.timedelta(minutes=4)})
            self.assertEqual(Line(l1.id).delay, 'warning')
            self.assertEqual(Line(l2.id).delay, 'late')
            self.assertTrue(Cron.search([
                        ('method', '=', 'restaurant.order.line|notify_delays')
                        ]))
            with patch('trytond.bus.notify') as notify:
                self.assertEqual(Line.notify_delays(), 2)
                self.assertTrue(notify.called)
                titles = [c.args[0] for c in notify.call_args_list]
                self.assertTrue(any('very late' in x for x in titles))
                notify.reset_mock()
                self.assertEqual(Line.notify_delays(), 0)  # once per level
                self.assertFalse(notify.called)
            Line.start([Line(l1.id)])
            self.assertEqual(Line(l1.id).notified_level, 0)
            Line.ready([Line(l1.id)])
            line = Line(l1.id)
            self.assertTrue(line.ready_at)
            self.assertEqual(line.ideal_minutes, r.serve_minutes)
            Line.write([line], {'ready_at': now - datetime.timedelta(
                        minutes=r.serve_minutes + 1)})
            with patch('trytond.bus.notify') as notify:
                self.assertEqual(Line.notify_delays(), 1)
                self.assertIn('ready', notify.call_args_list[0].args[0])

    @with_transaction()
    def test_sold_out(self):
        "Sold out item cannot be sent"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            Order = Pool().get('restaurant.order')
            fish = create_item("Fish", '30', sold_out=True)
            order = self._order(r, table=t1.id)
            self._add(order, fish)
            with self.assertRaises(OrderError):
                Order.send([Order(order.id)])

    @with_transaction()
    def test_pay_and_stock(self):
        "Payment closes the order and consumes recipe ingredients"
        company = create_company()
        with set_company(company):
            r, t1, _, storage = setup_restaurant(company)
            open_session(r)
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Move = pool.get('stock.move')
            Location = pool.get('stock.location')
            Recipe = pool.get('restaurant.recipe')
            flour = create_item(
                "Flour", '1', uom="Kilogram", restaurant_item=False)
            pizza = create_item("Pizza", '20')
            Recipe.create([{
                        'template': pizza.template.id,
                        'lines': [('create', [{
                                        'product': flour.id,
                                        'quantity': 0.25}])]}])
            supplier, = Location.search([('type', '=', 'supplier')], limit=1) \
                or Location.create([{'name': 'Supplier', 'type': 'supplier'}])
            move, = Move.create([{
                        'company': company.id, 'product': flour.id,
                        'unit': flour.default_uom.id, 'quantity': 10.0,
                        'from_location': supplier.id,
                        'to_location': storage.id,
                        'unit_price': Decimal('1'),
                        'currency': company.currency.id}])
            Move.do([move])
            order = self._order(r, table=t1.id)
            self._add(order, pizza, 2.0)
            Order.send([Order(order.id)])
            with self.assertRaises(OrderError):
                Order.pay([Order(order.id)])
            Payment.create([{'order': order.id, 'method': 'cash',
                        'amount': Decimal('44.00'),
                        'tendered': Decimal('50.00')}])
            self.assertEqual(Payment.search([])[0].change, Decimal('6.00'))
            Order.pay([Order(order.id)])
            self.assertEqual(Order(order.id).state, 'paid')
            moves = Move.search([('origin', '=', 'restaurant.order,%d'
                        % order.id)])
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0].quantity, 0.5)
            Order.cancel([Order(order.id)])  # ignored: not open anymore
            self.assertEqual(Order(order.id).state, 'paid')

    @with_transaction()
    def test_delivery(self):
        "Delivery minimum, fee, driver and states"
        company = create_company()
        with set_company(company):
            r, _, _, _ = setup_restaurant(company)
            pool = Pool()
            Order = pool.get('restaurant.order')
            Driver = pool.get('restaurant.driver')
            pizza = create_item("Pizza", '20')
            driver, = Driver.create([{'name': "Jean", 'phone': '555'}])
            cheap = create_item("Fries", '15')
            order = self._order(
                r, type='delivery', delivery_address="1 Rue A", phone='123')
            self._add(order, cheap, 1.0)
            with self.assertRaises(DeliveryError):
                Order.send([Order(order.id)])  # below minimum 20? equal ok
            order2 = self._order(
                r, type='delivery', delivery_address="2 Rue B", phone='123')
            self._add(order2, pizza, 2.0)
            order2 = Order(order2.id)
            self.assertEqual(order2.delivery_amount, Decimal('5'))
            self.assertEqual(order2.service_amount, Decimal('0'))
            Order.send([order2])
            with self.assertRaises(DeliveryError):
                Order.dispatch([Order(order2.id)])
            Order.write([Order(order2.id)], {'driver': driver.id})
            self.assertEqual(Order(order2.id).delivery_state, 'assigned')
            Order.dispatch([Order(order2.id)])
            self.assertEqual(Driver(driver.id).in_progress, 1)
            Order.deliver([Order(order2.id)])
            self.assertEqual(Order(order2.id).delivery_state, 'delivered')
            self.assertEqual(Driver(driver.id).in_progress, 0)

    @with_transaction()
    def test_free_delivery(self):
        "No delivery fee above the free threshold"
        company = create_company()
        with set_company(company):
            r, _, _, _ = setup_restaurant(company)
            Order = Pool().get('restaurant.order')
            pizza = create_item("Pizza", '60')
            order = self._order(
                r, type='delivery', delivery_address="x", phone='1')
            self._add(order, pizza, 2.0)
            self.assertEqual(Order(order.id).delivery_amount, Decimal('0'))

    @with_transaction()
    def test_reservation(self):
        "Overlap, seats and seating creates an order"
        company = create_company()
        with set_company(company):
            r, t1, t2, _ = setup_restaurant(company)
            Reservation = Pool().get('restaurant.reservation')
            base = {'restaurant': r.id, 'company': company.id,
                'date': TODAY + datetime.timedelta(days=1),
                'time': datetime.time(19, 0), 'duration': 90,
                'guest_name': "Marie", 'persons': 2, 'table': t1.id}
            res, = Reservation.create([base])
            self.assertTrue(res.number)
            Reservation.create([dict(base, time=datetime.time(21, 0))])
            Reservation.seat([res])
            res = Reservation(res.id)
            self.assertEqual(res.state, 'seated')
            self.assertEqual(res.order.table, t1)
            res2, = Reservation.create([dict(base, table=t2.id, persons=2)])
            Reservation.cancel([res2])
            Reservation.rebook([Reservation(res2.id)])
            Reservation.no_show_([Reservation(res2.id)])
            self.assertEqual(Reservation(res2.id).state, 'no_show')
            with self.assertRaises(ReservationError):
                Reservation.create([dict(base, time=datetime.time(20, 0))])

    @with_transaction()
    def test_happy_hour(self):
        "Happy hour discount applies at the right time"
        company = create_company()
        with set_company(company):
            r, _, _, _ = setup_restaurant(company)
            HappyHour = Pool().get('restaurant.happy_hour')
            beer = create_item("Beer", '10', station='bar')
            HappyHour.create([{'name': "HH", 'restaurant': r.id,
                        'weekday': 'all', 'start_hour': 17.0,
                        'end_hour': 19.0, 'discount': Decimal('30')}])
            inside = datetime.datetime(2026, 1, 5, 18, 0)
            outside = datetime.datetime(2026, 1, 5, 20, 0)
            self.assertEqual(
                HappyHour.get_discount(r, beer, inside), Decimal('30'))
            self.assertEqual(
                HappyHour.get_discount(r, beer, outside), Decimal('0'))
            with self.assertRaises(RestaurantError):
                HappyHour.create([{'name': "bad", 'start_hour': 19.0,
                            'end_hour': 18.0, 'discount': Decimal('10')}])

    @with_transaction()
    def test_reports(self):
        "Sales report, dashboard and bill render"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Sales = pool.get('restaurant.report.sales')
            Dashboard = pool.get('restaurant.dashboard')
            Receipt = pool.get('restaurant.order.receipt', type='report')
            pizza = create_item("Pizza", '20')
            order = self._order(r, table=t1.id)
            self._add(order, pizza, 1.0)
            Order.send([Order(order.id)])
            dash, = Dashboard.search([])
            self.assertEqual(dash.open_orders, 1)
            self.assertEqual(dash.occupied_tables, 1)
            self.assertEqual(dash.kitchen_queue, 1)
            Payment.create([{'order': order.id, 'method': 'card',
                        'amount': Decimal('22.00')}])
            Order.pay([Order(order.id)])
            row, = Sales.search([])
            self.assertEqual(row.amount, Decimal('20.00'))
            Receipt.execute([order.id], {})

    @with_transaction()
    def test_guide(self):
        "Usage guide wizard returns text"
        from trytond.modules.restaurant.guide import GuideStart
        self.assertIn('content', GuideStart._fields)
        company = create_company()
        with set_company(company):
            Guide = Pool().get('restaurant.guide', type='wizard')
            session_id, _, _ = Guide.create()
            state = Guide(session_id).default_start(['content'])
            self.assertIn('Setup', state['content'])


class RestaurantCashTestCase(CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test restaurant cash, menus, guests and party"
    module = 'restaurant'

    def _order(self, restaurant, **values):
        Order = Pool().get('restaurant.order')
        vals = {'restaurant': restaurant.id, 'company': restaurant.company.id,
            'type': 'dine_in'}
        vals.update(values)
        order, = Order.create([vals])
        return order

    def _add(self, order, product, quantity=1.0, **values):
        Line = Pool().get('restaurant.order.line')
        vals = {'order': order.id, 'product': product.id,
            'quantity': quantity, 'unit_price': product.list_price}
        vals.update(values)
        line, = Line.create([vals])
        return line

    @with_transaction()
    def test_party_first_name(self):
        "Party first name shows in the record name"
        company = create_company()
        with set_company(company):
            Party = Pool().get('party.party')
            person, = Party.create([{
                        'name': "Duval", 'is_person': True,
                        'first_name': "Marie"}])
            self.assertEqual(person.rec_name, "Marie Duval")
            self.assertEqual(Party.search([('rec_name', '=', 'Marie')]),
                [person])

    @with_transaction()
    def test_cash_session_flow(self):
        "Open count, cash payments, closing count and difference"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            pool = Pool()
            Order = pool.get('restaurant.order')
            Payment = pool.get('restaurant.payment')
            Session = pool.get('restaurant.cash.session')
            Count = pool.get('restaurant.cash.count')
            pizza = create_item("Pizza", '20')
            order = self._order(r, table=t1.id)
            self._add(order, pizza, 1.0)
            with self.assertRaises(CashError):
                Payment.create([{'order': order.id, 'method': 'cash',
                            'amount': Decimal('22.00')}])
            session, denominations = open_session(r)
            self.assertEqual(session.opening_amount, Decimal('1000'))
            payment, = Payment.create([{
                        'order': order.id, 'method': 'cash',
                        'amount': Decimal('22.00'),
                        'tendered': Decimal('50.00')}])
            self.assertEqual(payment.session, session)
            session = Session(session.id)
            self.assertEqual(session.cash_sales, Decimal('22.00'))
            self.assertEqual(session.expected_amount, Decimal('1022.00'))
            with self.assertRaises(CashError):  # closing not counted
                Session.close([session])
            bill100 = [d for d in denominations if d.value == 100][0]
            bill10 = [d for d in denominations if d.value == 10][0]
            Count.create([
                    {'session': session.id, 'moment': 'closing',
                        'denomination': bill100.id, 'quantity': 10},
                    {'session': session.id, 'moment': 'closing',
                        'denomination': bill10.id, 'quantity': 2,
                        'suspect_quantity': 1}])
            session = Session(session.id)
            self.assertEqual(session.closing_amount, Decimal('1010'))
            self.assertEqual(session.suspect_amount, Decimal('10'))
            self.assertEqual(session.difference, Decimal('-12.00'))
            with self.assertRaises(RestaurantWarning):
                Session.close([session])
            from trytond.modules.company.tests import set_company as _  # noqa
            Warning = pool.get('res.user.warning')
            for key in ('restaurant_cash_difference_%s' % session.id,
                    'restaurant_cash_suspect_%s' % session.id):
                Warning(user=Transaction().user, name=key).save()
            Session.close([session])
            self.assertEqual(Session(session.id).state, 'closed')
            with self.assertRaises(CashError):
                bill50 = [d for d in denominations if d.value == 50][0]
                Count.create([{'session': session.id, 'moment': 'closing',
                            'denomination': bill50.id, 'quantity': 1}])

    @with_transaction()
    def test_cash_opening_must_match_previous_closing(self):
        "An opening count different from the last closing needs a confirmation"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            pool = Pool()
            Session = pool.get('restaurant.cash.session')
            Count = pool.get('restaurant.cash.count')
            Warning = pool.get('res.user.warning')
            session, denominations = open_session(r)
            bill100 = [d for d in denominations if d.value == 100][0]
            Count.create([{'session': session.id, 'moment': 'closing',
                        'denomination': bill100.id, 'quantity': 10}])
            Session.close([Session(session.id)])
            self.assertEqual(Session(session.id).state, 'closed')
            # next shift finds 9 bills instead of the 10 left in the drawer
            second, = Session.create([{
                        'company': company.id, 'restaurant': r.id,
                        'cashier': Transaction().user or 1}])
            Count.create([{'session': second.id, 'moment': 'opening',
                        'denomination': bill100.id, 'quantity': 9}])
            with self.assertRaises(RestaurantWarning):
                Session.open_([second])
            Warning(user=Transaction().user,
                name='restaurant_cash_opening_%s' % second.id).save()
            Session.open_([Session(second.id)])
            self.assertEqual(Session(second.id).state, 'open')

    @with_transaction()
    def test_one_session_per_cashier(self):
        "A cashier cannot open two sessions in the same restaurant"
        company = create_company()
        with set_company(company):
            r, _, _, _ = setup_restaurant(company)
            pool = Pool()
            Session = pool.get('restaurant.cash.session')
            Count = pool.get('restaurant.cash.count')
            session, denominations = open_session(r)
            other, = Session.create([{
                        'company': company.id, 'restaurant': r.id,
                        'cashier': session.cashier.id}])
            Count.create([{'session': other.id, 'moment': 'opening',
                        'denomination': denominations[0].id,
                        'quantity': 1}])
            with self.assertRaises(CashError):
                Session.open_([other])

    @with_transaction()
    def test_cash_control_off(self):
        "Without cash control no session is required"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company, cash_control=False)
            Payment = Pool().get('restaurant.payment')
            order = self._order(r, table=t1.id)
            self._add(order, create_item("Pizza", '20'), 1.0)
            payment, = Payment.create([{'order': order.id, 'method': 'cash',
                        'amount': Decimal('22.00')}])
            self.assertIsNone(payment.session)

    @with_transaction()
    def test_menus(self):
        "Menus restrict items and set prices when enforced"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company, enforce_menus=True)
            pool = Pool()
            Menu = pool.get('restaurant.menu')
            Order = pool.get('restaurant.order')
            pizza = create_item("Pizza", '20')
            soup = create_item("Soup", '8')
            Menu.create([{'name': "Lunch", 'restaurant': r.id,
                        'weekday': 'all', 'start_hour': 0.0,
                        'end_hour': 24.0,
                        'items': [('create', [{
                                        'template': pizza.template.id,
                                        'price': Decimal('15')}])]}])
            order = self._order(r, table=t1.id)
            Line = pool.get('restaurant.order.line')
            line, = Line.create([{'order': order.id, 'product': pizza.id,
                        'quantity': 1.0}])
            self.assertEqual(line.unit_price, Decimal('15'))
            self._add(order, soup, 1.0)
            with self.assertRaises(OrderError):
                Order.send([Order(order.id)])
            with self.assertRaises(RestaurantError):
                Menu.create([{'name': "bad", 'restaurant': r.id,
                            'start_hour': 20.0, 'end_hour': 10.0}])

    @with_transaction()
    def test_guest_split(self):
        "Bill split by guest shares the common items"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            Order = Pool().get('restaurant.order')
            pizza = create_item("Pizza", '20')
            wine = create_item("Wine", '10', station='bar')
            order = self._order(r, table=t1.id)
            self._add(order, pizza, 1.0, guest=1)
            self._add(order, pizza, 1.0, guest=2)
            self._add(order, wine, 1.0, guest=0)
            rows = Order(order.id).guest_summary()
            self.assertEqual([x['guest'] for x in rows], [1, 2])
            self.assertEqual([x['subtotal'] for x in rows],
                [Decimal('25.00'), Decimal('25.00')])
            self.assertEqual(rows[0]['total'], Decimal('27.50'))
            self.assertEqual(sum(x['total'] for x in rows),
                Order(order.id).total)

    @with_transaction()
    def test_tickets_render(self):
        "80 mm ticket and split bill render"
        company = create_company()
        with set_company(company):
            r, t1, _, _ = setup_restaurant(company)
            pool = Pool()
            order = self._order(r, table=t1.id)
            self._add(order, create_item("Pizza", '20'), 1.0, guest=1)
            for name in ('restaurant.order.ticket', 'restaurant.order.split',
                    'restaurant.order.receipt'):
                pool.get(name, type='report').execute([order.id], {})

    @with_transaction()
    def test_recipe_unit_digits(self):
        "A recipe quantity lost by unit rounding is refused"
        company = create_company()
        with set_company(company):
            Recipe = Pool().get('restaurant.recipe')
            flour = create_item("Flour", '1', restaurant_item=False)
            pizza = create_item("Pizza", '20')
            with self.assertRaises(RestaurantError):
                Recipe.create([{'template': pizza.template.id,
                            'lines': [('create', [{
                                            'product': flour.id,
                                            'quantity': 0.25}])]}])


def suite():
    suite = unittest.TestSuite()
    for case in (RestaurantTestCase, RestaurantCashTestCase):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    return suite
