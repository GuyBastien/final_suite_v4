# This file is part of the Tryton hotel_rates_plus module.
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

from ..exceptions import RatePlusError

START = datetime.datetime(2030, 1, 7, 14, 0)    # Monday
END = datetime.datetime(2030, 1, 9, 11, 0)      # 2 nights
D = datetime.date


class RatesPlusTestCase(CompanyTestMixin, ModuleTestCase):
    "Test hotel_rates_plus module"
    module = 'hotel_rates_plus'

    def _category(self):
        pool = Pool()
        Category = pool.get('product.category')
        Account = pool.get('account.account')
        categories = Category.search([('name', '=', "Hotel")])
        if categories:
            return categories[0].id
        create_chart(pool.get('company.company')(
                Transaction().context['company']))
        revenue, = Account.search([('type.revenue', '=', True)], limit=1)
        expense, = Account.search([('type.expense', '=', True)], limit=1)
        category, = Category.create([{
                    'name': "Hotel", 'accounting': True,
                    'account_revenue': revenue.id,
                    'account_expense': expense.id}])
        return category.id

    def _service(self, name, price):
        pool = Pool()
        Template = pool.get('product.template')
        Uom = pool.get('product.uom')
        unit, = Uom.search([('name', '=', "Unit")])
        template, = Template.create([{
                    'name': name, 'type': 'service',
                    'account_category': self._category(),
                    'default_uom': unit.id, 'sale_uom': unit.id,
                    'salable': True, 'list_price': price,
                    'products': [('create', [{}])],
                    }])
        return template.products[0]

    def _setup(self, company):
        pool = Pool()
        Room = pool.get('hotel.room')
        Party = pool.get('party.party')
        hotel = create_hotel(company)
        room_type = create_room_type(hotel)
        room_type.night_product = self._service("Night", Decimal('100'))
        room_type.hour_product = self._service("Hour", Decimal('20'))
        room_type.save()
        rooms = Room.create([{
                    'hotel': hotel.id, 'name': n, 'type': room_type.id}
                for n in ('101', '102')])
        party, = Party.create([{'name': "Guest"}])
        return hotel, room_type, rooms, party

    def _reservation(self, company, hotel, party, room_type, room,
            start=START, end=END, **values):
        Reservation = Pool().get('hotel.reservation')
        values.update({
                'company': company.id, 'hotel': hotel.id,
                'party': party.id, 'start': start, 'end': end,
                'lines': [('create', [{
                                'room_type': room_type.id,
                                'room': room.id}])],
                })
        return Reservation.create([values])[0]

    def _restriction(self, hotel, **values):
        Restriction = Pool().get('hotel.rate.restriction')
        values['hotel'] = hotel.id
        return Restriction.create([values])[0]

    @with_transaction()
    def test_restriction_min_max_stay(self):
        "Test minimum and maximum stay block the confirmation"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            restriction = self._restriction(
                hotel, start_date=D(2030, 1, 1), end_date=D(2030, 1, 31),
                min_stay=3)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            with self.assertRaises(RatePlusError):
                Reservation.confirm([reservation])
            restriction.min_stay = 2
            restriction.max_stay = 1
            restriction.save()
            with self.assertRaises(RatePlusError):
                Reservation.confirm([reservation])
            restriction.max_stay = 5
            restriction.save()
            Reservation.confirm([reservation])
            self.assertEqual(reservation.state, 'confirmed')
            # Outside of the period the restriction does not apply
            later = self._reservation(
                company, hotel, party, room_type, room1,
                start=datetime.datetime(2030, 3, 1, 14),
                end=datetime.datetime(2030, 3, 2, 11))
            Reservation.confirm([later])
            self.assertEqual(later.state, 'confirmed')

    @with_transaction()
    def test_restriction_closed_and_arrival_departure(self):
        "Test closed, closed to arrival and closed to departure"
        company = create_company()
        with set_company(company):
            Reservation = Pool().get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1)
            # Closed to arrival on the arrival day only
            cta = self._restriction(
                hotel, start_date=D(2030, 1, 7), end_date=D(2030, 1, 7),
                closed_to_arrival=True)
            with self.assertRaises(RatePlusError):
                Reservation.confirm([reservation])
            cta.closed_to_arrival = False
            cta.closed_to_departure = True
            cta.start_date = cta.end_date = D(2030, 1, 9)
            cta.save()
            with self.assertRaises(RatePlusError):
                Reservation.confirm([reservation])
            # Closed to departure on the day before does not matter
            cta.start_date = cta.end_date = D(2030, 1, 8)
            cta.save()
            Reservation.confirm([reservation])
            Reservation.draft([reservation])
            # A closed night blocks the stay
            cta.closed_to_departure = False
            cta.closed = True
            cta.save()
            with self.assertRaises(RatePlusError):
                Reservation.confirm([reservation])
            # ... unless the restriction is for another room type
            RoomType = Pool().get('hotel.room.type')
            other, = RoomType.create([{
                        'hotel': hotel.id, 'name': "Suite", 'code': "STE",
                        'capacity': 4}])
            cta.room_type = other
            cta.save()
            Reservation.confirm([reservation])
            self.assertEqual(reservation.state, 'confirmed')

    @with_transaction()
    def test_meal_plan_charges(self):
        "Test the meal plan creates and replaces the meal charges"
        company = create_company()
        with set_company(company):
            pool = Pool()
            MealPlan = pool.get('hotel.meal.plan')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            meal = self._service("Breakfast", Decimal('10'))
            plan, = MealPlan.create([{
                        'hotel': hotel.id, 'name': "Bed and Breakfast",
                        'code': "BB", 'product': meal.id,
                        'adult_price': Decimal('12'),
                        'child_price': Decimal('6')}])
            reservation = self._reservation(
                company, hotel, party, room_type, room1,
                adults=2, children=1, meal_plan=plan.id)
            Reservation.compute([reservation])
            Reservation.add_meal_plan([reservation])
            charges = sorted(reservation.charges, key=lambda c: c.unit_price)
            self.assertEqual(
                [(c.quantity, c.unit_price, c.rates_plus_kind)
                    for c in charges],
                [(2.0, Decimal('6'), 'meal_child'),
                    (4.0, Decimal('12'), 'meal_adult')])
            self.assertEqual(reservation.total_amount, Decimal('260.00'))
            # Using the button again replaces the charges
            Reservation.add_meal_plan([reservation])
            self.assertEqual(len(reservation.charges), 2)
            self.assertEqual(reservation.total_amount, Decimal('260.00'))
            # An included plan charges nothing
            plan.included = True
            plan.save()
            Reservation.add_meal_plan([reservation])
            self.assertEqual(reservation.charges, ())
            # No product: the plan cannot be charged
            plan.included = False
            plan.product = None
            plan.save() if False else None
            with self.assertRaises(Exception):
                plan.save()

    @with_transaction()
    def test_supplement_extra_guests(self):
        "Test the supplement charges the guests above the capacity"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Supplement = pool.get('hotel.rate.supplement')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            reservation = self._reservation(
                company, hotel, party, room_type, room1, adults=3,
                children=1)
            with self.assertRaises(RatePlusError):
                Reservation.add_supplements([reservation])
            product = self._service("Extra bed", Decimal('30'))
            Supplement.create([{
                        'room_type': room_type.id, 'product': product.id,
                        'extra_adult_price': Decimal('30'),
                        'extra_child_price': Decimal('15')}])
            Reservation.add_supplements([reservation])
            charges = sorted(reservation.charges, key=lambda c: c.unit_price)
            # Capacity 2: one extra adult and one extra child, 2 nights
            self.assertEqual(
                [(c.quantity, c.unit_price, c.rates_plus_kind)
                    for c in charges],
                [(2.0, Decimal('15'), 'supplement_child'),
                    (2.0, Decimal('30'), 'supplement_adult')])
            Reservation.add_supplements([reservation])
            self.assertEqual(len(reservation.charges), 2)
            # Within the capacity nothing is charged
            reservation.adults, reservation.children = 2, 0
            reservation.save()
            Reservation.add_supplements([reservation])
            self.assertEqual(reservation.charges, ())

    @with_transaction()
    def test_contract_discount_and_fixed_rate(self):
        "Test the contract applies to its customer only"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Contract = pool.get('hotel.rate.contract')
            Party = pool.get('party.party')
            Rate = pool.get('hotel.rate')
            Reservation = pool.get('hotel.reservation')
            Line = pool.get('hotel.reservation.line')
            hotel, room_type, (room1, room2), party = self._setup(company)
            corporate, = Party.create([{'name': "ACME"}])
            other, = Party.create([{'name': "Nobody"}])
            with self.assertRaises(RatePlusError):
                Contract.create([{'hotel': hotel.id, 'party': other.id}])
            contract, = Contract.create([{
                        'hotel': hotel.id, 'party': corporate.id,
                        'discount_rate': Decimal('0.1')}])
            self.assertEqual(contract.rec_name, "ACME - Grand Hotel")
            public = self._reservation(
                company, hotel, party, room_type, room1)
            Reservation.compute([public])
            self.assertEqual(public.lines[0].discount_rate, Decimal(0))
            self.assertEqual(public.total_amount, Decimal('200.00'))
            business = self._reservation(
                company, hotel, corporate, room_type, room2)
            Reservation.compute([business])
            self.assertEqual(
                business.lines[0].discount_rate, Decimal('0.1'))
            self.assertEqual(business.total_amount, Decimal('180.00'))
            # A fixed rate replaces the public rate
            corporate_rate, = Rate.create([{
                        'room_type': room_type.id, 'name': "ACME",
                        'night_price': Decimal('80'), 'min_hours': 1,
                        'max_hours': 12, 'active': True,
                        'start_date': D(2031, 1, 1)}])
            contract.rate = corporate_rate
            contract.discount_rate = None
            contract.save()
            Line.write(list(business.lines), {'discount_rate': 0})
            Reservation.compute([business])
            line, = business.lines
            self.assertEqual(line.unit_price, Decimal('80'))
            self.assertEqual(business.total_amount, Decimal('160.00'))
            # An expired contract does not apply
            contract.end_date = D(2029, 12, 31)
            contract.save()
            Line.write(list(business.lines), {'discount_rate': 0})
            Reservation.compute([business])
            self.assertEqual(business.total_amount, Decimal('200.00'))
            # The room type contract wins over the general contract
            contract.end_date = None
            contract.save()
            specific, = Contract.create([{
                        'hotel': hotel.id, 'party': corporate.id,
                        'room_type': room_type.id,
                        'discount_rate': Decimal('0.5')}])
            self.assertEqual(
                Contract.get_for(hotel, corporate, room_type, START.date()),
                specific)

    @with_transaction()
    def test_pricing_rule_occupancy_and_lead_time(self):
        "Test the dynamic pricing follows occupancy and booking lead time"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Rule = pool.get('hotel.pricing.rule')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            Rule.create([{
                        'hotel': hotel.id, 'name': "Busy", 'priority': 10,
                        'occupancy_from': 40, 'occupancy_to': 100,
                        'adjustment_percent': Decimal('10')},
                    {'hotel': hotel.id, 'name': "Very busy",
                        'priority': 20, 'occupancy_from': 90,
                        'occupancy_to': 100,
                        'adjustment_percent': Decimal('25')},
                    {'hotel': hotel.id, 'name': "Early bird",
                        'priority': 5, 'occupancy_from': 0,
                        'occupancy_to': 39, 'advance_days_min': 30,
                        'adjustment_percent': Decimal('-10')}])
            first = self._reservation(
                company, hotel, party, room_type, room1)
            # Dynamic pricing is off by default
            Reservation.confirm([first])
            self.assertEqual(first.total_amount, Decimal('200.00'))
            hotel.dynamic_pricing = True
            hotel.save()
            # Empty hotel and far arrival: early bird -10%
            Reservation.cancel([first])
            Reservation.draft([first])
            Reservation.compute([first])
            self.assertEqual(first.lines[0].unit_price, Decimal('90.00'))
            Reservation.confirm([first])
            # One room out of two is sold: 50% -> Busy +10%
            second = self._reservation(
                company, hotel, party, room_type, room2)
            Reservation.compute([second])
            self.assertEqual(second.lines[0].unit_price, Decimal('110.00'))
            self.assertEqual(
                Rule.occupancy(hotel, START, END), Decimal(50))
            self.assertEqual(
                Rule.adjustment(hotel, START, END, 1000), Decimal('10'))
            # Lead time below the early bird minimum and low occupancy
            self.assertEqual(
                Rule.adjustment(hotel, START, END, 1000, exclude=first),
                Decimal('-10'))
            self.assertEqual(
                Rule.adjustment(hotel, START, END, 5, exclude=first),
                Decimal(0))

    @with_transaction()
    def test_revenue_kpi(self):
        "Test the revenue indicators of the hotel"
        company = create_company()
        with set_company(company):
            pool = Pool()
            KPI = pool.get('hotel.revenue.kpi')
            Reservation = pool.get('hotel.reservation')
            hotel, room_type, (room1, room2), party = self._setup(company)
            today = datetime.date.today()
            start = datetime.datetime.combine(
                today, datetime.time(14, 0))
            reservation = self._reservation(
                company, hotel, party, room_type, room1,
                start=start, end=start + datetime.timedelta(days=2, hours=-3))
            Reservation.confirm([reservation])
            kpi, = KPI.search([('hotel', '=', hotel.id)])
            self.assertEqual(kpi.rec_name, hotel.rec_name)
            self.assertEqual(kpi.rooms_total, 2)
            # One of two rooms sold today, 100 per night
            self.assertEqual(kpi.occupancy_today, Decimal('50.0'))
            self.assertEqual(kpi.revenue_today, Decimal('100.00'))
            self.assertEqual(kpi.adr_today, Decimal('100.00'))
            self.assertEqual(kpi.revpar_today, Decimal('50.00'))
            # Forecast of the next 30 days: 2 room-nights of 60
            self.assertEqual(kpi.forecast_sold, 2)
            self.assertEqual(kpi.forecast_revenue, Decimal('200.00'))
            self.assertEqual(
                kpi.forecast_occupancy, Decimal(2 * 100) / 60)
