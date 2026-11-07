# This file is part of the Tryton fuel_station module.
import datetime
import unittest
from decimal import Decimal

from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.model.exceptions import ValidationError
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction

from ..exceptions import FuelError, FuelWarning

TODAY = datetime.date.today()


def create_station(company, code="S1"):
    pool = Pool()
    Location = pool.get('stock.location')
    Station = pool.get('fuel.station')
    if not Location.search([('type', '=', 'customer')]):
        Location.create([{'name': "Customer", 'type': 'customer'}])
    inp, out, storage = Location.create([
            {'name': "Input", 'type': 'storage'},
            {'name': "Output", 'type': 'storage'},
            {'name': "Storage", 'type': 'storage'}])
    warehouse, = Location.create([{
                'name': "Warehouse " + code, 'code': 'WH' + code,
                'type': 'warehouse', 'input_location': inp.id,
                'output_location': out.id, 'storage_location': storage.id}])
    station, = Station.create([{
                'code': code, 'name': "Main Station", 'company': company.id,
                'warehouse': warehouse.id,
                }])
    return station


def create_fuel(name="Diesel", cost=Decimal('1.00')):
    pool = Pool()
    Template = pool.get('product.template')
    Uom = pool.get('product.uom')
    gallon, = Uom.search([('name', '=', "Gallon")])
    template, = Template.create([{
                'name': name, 'type': 'goods', 'fuel': True,
                'default_uom': gallon.id, 'list_price': Decimal('2'),
                'products': [('create', [{'cost_price': cost}])]}])
    return template.products[0]


class FuelStationTestCase(CompanyTestMixin, ModuleTestCase):
    "Test fuel_station module"
    module = 'fuel_station'

    def _setup(self, company, tank_capacity=10000.0):
        pool = Pool()
        Tank = pool.get('fuel.tank')
        Pump = pool.get('fuel.pump')
        Nozzle = pool.get('fuel.nozzle')
        Price = pool.get('fuel.price')
        Employee = pool.get('company.employee')
        Party = pool.get('party.party')
        station = create_station(company)
        diesel = create_fuel("Diesel")
        gasoline = create_fuel("Gasoline")
        tank1, tank2 = Tank.create([
                {'station': station.id, 'code': "T1", 'product': diesel.id,
                    'capacity': tank_capacity, 'min_level': 1000.0},
                {'station': station.id, 'code': "T2",
                    'product': gasoline.id, 'capacity': tank_capacity,
                    'min_level': 1000.0}])
        pump, = Pump.create([{'station': station.id, 'code': "P1"}])
        n1, n2 = Nozzle.create([
                {'pump': pump.id, 'number': 1, 'tank': tank1.id,
                    'meter_index': 1000.0},
                {'pump': pump.id, 'number': 2, 'tank': tank2.id,
                    'meter_index': 5000.0}])
        Price.create([
                {'company': company.id, 'product': diesel.id,
                    'date': TODAY - datetime.timedelta(days=30),
                    'price': Decimal('2.50')},
                {'company': company.id, 'product': gasoline.id,
                    'date': TODAY - datetime.timedelta(days=30),
                    'price': Decimal('3.00')}])
        person, = Party.create([{'name': "Pump Attendant"}])
        employee, = Employee.create([{
                    'party': person.id, 'company': company.id}])
        supplier, = Party.create([{'name': "Fuel Supplier"}])
        return {
            'station': station, 'diesel': diesel, 'gasoline': gasoline,
            'tank1': tank1, 'tank2': tank2, 'n1': n1, 'n2': n2,
            'employee': employee, 'supplier': supplier}

    def _fill(self, company, ctx, tank, quantity, cost=Decimal('1.20')):
        pool = Pool()
        Delivery = pool.get('fuel.delivery')
        delivery, = Delivery.create([{
                    'company': company.id, 'station': ctx['station'].id,
                    'supplier': ctx['supplier'].id, 'date': TODAY,
                    'lines': [('create', [{
                                    'tank': tank.id, 'quantity': quantity,
                                    'unit_cost': cost}])]}])
        Delivery.receive([delivery])
        return delivery

    def _shift(self, company, ctx, **values):
        Shift = Pool().get('fuel.shift')
        values.update(company=company.id, station=ctx['station'].id,
            attendant=ctx['employee'].id, date=TODAY)
        return Shift.create([values])[0]

    @with_transaction()
    def test_station_and_tank(self):
        "Test the tank gets its stock location and figures"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Tank = pool.get('fuel.tank')
            ctx = self._setup(company)
            station, tank = ctx['station'], ctx['tank1']
            self.assertEqual(station.rec_name, "[S1] Main Station")
            self.assertEqual(station.currency, company.currency)
            self.assertEqual(
                tank.location.parent, station.warehouse.storage_location)
            self.assertEqual(tank.quantity, 0.0)
            self.assertTrue(tank.low)
            self.assertEqual(tank.unit.name, "Gallon")
            self._fill(company, ctx, tank, 4000.0)
            tank = Tank(tank.id)
            self.assertEqual(tank.quantity, 4000.0)
            self.assertAlmostEqual(tank.fill_ratio, 0.4)
            self.assertFalse(tank.low)
            self.assertEqual(Tank.search([('low', '=', True)]), [
                    ctx['tank2']])
            self.assertEqual(Tank.search([('low', '=', False)]), [tank])

    @with_transaction()
    def test_tank_constraints(self):
        "Test the tank constraints"
        company = create_company()
        with set_company(company):
            Tank = Pool().get('fuel.tank')
            ctx = self._setup(company)
            with self.assertRaises(Exception):  # duplicated code
                Tank.create([{
                            'station': ctx['station'].id, 'code': "T1",
                            'product': ctx['diesel'].id, 'capacity': 100.0,
                            'min_level': 0.0}])

    @with_transaction()
    def test_price_lookup(self):
        "Test the price of the station wins over the company price"
        company = create_company()
        with set_company(company):
            Price = Pool().get('fuel.price')
            ctx = self._setup(company)
            station, diesel = ctx['station'], ctx['diesel']
            self.assertEqual(
                Price.get_price(station, diesel, TODAY), Decimal('2.50'))
            Price.create([
                    {'company': company.id, 'station': station.id,
                        'product': diesel.id, 'date': TODAY,
                        'price': Decimal('2.75')},
                    {'company': company.id, 'product': diesel.id,
                        'date': TODAY + datetime.timedelta(days=1),
                        'price': Decimal('9')}])
            self.assertEqual(
                Price.get_price(station, diesel, TODAY), Decimal('2.75'))
            with self.assertRaises(FuelError):
                Price.get_price(
                    station, diesel, TODAY - datetime.timedelta(days=90))

    @with_transaction()
    def test_shift_cycle(self):
        "Test a complete shift: open, sell, pay, close"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Tank = pool.get('fuel.tank')
            Nozzle = pool.get('fuel.nozzle')
            Move = pool.get('stock.move')
            ctx = self._setup(company)
            ctx['station'].cash_tolerance = Decimal('50')
            ctx['station'].save()
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._shift(company, ctx)
            self.assertTrue(shift.number)
            Shift.open_shift([shift])
            shift = Shift(shift.id)
            self.assertEqual(shift.state, 'open')
            self.assertEqual(len(shift.lines), 2)
            line1, line2 = shift.lines
            self.assertEqual(line1.opening_index, 1000.0)
            self.assertEqual(line1.unit_price, Decimal('2.50'))
            self.assertEqual(line2.unit_price, Decimal('3.00'))
            # Closing without index is refused
            with self.assertRaises(FuelError):
                Shift.close([shift])
            line1.closing_index = 1300.0
            line1.test_quantity = 10.0
            line1.save()
            line2.closing_index = 5100.0
            line2.save()
            Shift.write([shift], {'payments': [('create', [
                            {'method': 'cash', 'amount': Decimal('1000')},
                            {'method': 'card', 'amount': Decimal('50')}])]})
            shift = Shift(shift.id)
            self.assertEqual(shift.total_paid, Decimal('1050'))
            Shift.close([shift])
            shift = Shift(shift.id)
            line1, line2 = shift.lines
            self.assertEqual(shift.state, 'closed')
            self.assertEqual(line1.quantity, 290.0)
            self.assertEqual(line1.amount, Decimal('725.00'))
            self.assertEqual(line2.quantity, 100.0)
            self.assertEqual(line2.amount, Decimal('300.00'))
            self.assertEqual(shift.total_sales, Decimal('1025.00'))
            self.assertEqual(shift.total_quantity, 390.0)
            self.assertEqual(shift.difference, Decimal('25.00'))
            self.assertTrue(shift.end_time)
            self.assertEqual(line1.move.state, 'done')
            self.assertEqual(line1.move.origin, line1)
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 4710.0)
            self.assertEqual(Tank(ctx['tank2'].id).quantity, 4900.0)
            self.assertEqual(Nozzle(ctx['n1'].id).meter_index, 1300.0)
            # A closed shift is frozen
            with self.assertRaises(FuelError):
                Shift.delete([shift])
            with self.assertRaises(FuelError):
                Pool().get('fuel.shift.payment').create([{
                            'shift': shift.id, 'method': 'cash',
                            'amount': Decimal(1)}])
            self.assertEqual(len(Move.search([
                            ('origin', 'like', 'fuel.shift.line,%')])), 2)

    @with_transaction()
    def test_shift_rules(self):
        "Test one open shift per station and the index checks"
        company = create_company()
        with set_company(company):
            Shift = Pool().get('fuel.shift')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            first = self._shift(company, ctx)
            second = self._shift(company, ctx)
            Shift.open_shift([first])
            with self.assertRaises(FuelError):
                Shift.open_shift([second])
            first = Shift(first.id)
            line = first.lines[0]
            line.closing_index = 900.0  # lower than the opening index
            with self.assertRaises(Exception):
                line.save()
            line = first.lines[0]
            line.closing_index = 1010.0
            line.test_quantity = 50.0  # negative sale
            line.save()
            other = first.lines[1]
            other.closing_index = other.opening_index
            other.save()
            with self.assertRaises(FuelError):
                Shift.close([Shift(first.id)])
            Shift.cancel([Shift(first.id)])
            Shift.open_shift([Shift(second.id)])

    @with_transaction()
    def test_shift_cash_difference_warning(self):
        "Test a cash difference above the tolerance asks a confirmation"
        company = create_company()
        with set_company(company):
            Shift = Pool().get('fuel.shift')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._shift(company, ctx)
            Shift.open_shift([shift])
            for line in Shift(shift.id).lines:
                line.closing_index = line.opening_index + 100
                line.save()
            with self.assertRaises(FuelWarning):
                Shift.close([Shift(shift.id)])
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([Shift(shift.id)])
            self.assertEqual(Shift(shift.id).state, 'closed')
            self.assertEqual(Shift(shift.id).difference, Decimal('-550.00'))

    @with_transaction()
    def test_delivery(self):
        "Test a delivery feeds the tank, overfilling asks a confirmation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Delivery = pool.get('fuel.delivery')
            Tank = pool.get('fuel.tank')
            ctx = self._setup(company, tank_capacity=5000.0)
            delivery = self._fill(company, ctx, ctx['tank1'], 3000.0)
            self.assertEqual(Delivery(delivery.id).state, 'done')
            line = Delivery(delivery.id).lines[0]
            self.assertEqual(line.move.state, 'done')
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 3000.0)
            with self.assertRaises(FuelError):
                Delivery.delete([Delivery(delivery.id)])
            with self.assertRaises(FuelWarning):
                self._fill(company, ctx, ctx['tank1'], 3000.0)
            empty, = Delivery.create([{
                        'company': company.id,
                        'station': ctx['station'].id,
                        'supplier': ctx['supplier'].id, 'date': TODAY}])
            with self.assertRaises(FuelError):
                Delivery.receive([empty])
            Delivery.cancel([empty])
            Delivery.delete([Delivery(empty.id)])

    @with_transaction()
    def test_delivery_foreign_tank(self):
        "Test a delivery cannot feed the tank of another station"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Delivery = pool.get('fuel.delivery')
            Tank = pool.get('fuel.tank')
            ctx = self._setup(company)
            other = create_station(company, "S2")
            tank, = Tank.create([{
                        'station': other.id, 'code': "X",
                        'product': ctx['diesel'].id, 'capacity': 100.0}])
            with self.assertRaises(ValidationError):
                Delivery.create([{
                            'company': company.id,
                            'station': ctx['station'].id,
                            'supplier': ctx['supplier'].id, 'date': TODAY,
                            'lines': [('create', [{
                                            'tank': tank.id,
                                            'quantity': 10.0,
                                            'unit_cost': Decimal(1)}])]}])

    @with_transaction()
    def test_dip(self):
        "Test the dip variance, tolerance and stock adjustment"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Dip = pool.get('fuel.dip')
            Tank = pool.get('fuel.tank')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 4000.0)
            values = {
                'company': company.id, 'station': ctx['station'].id,
                'tank': ctx['tank1'].id, 'date': TODAY}
            ok, = Dip.create([dict(values, measured_quantity=3980.0)])
            Dip.validate_dip([ok])
            ok = Dip(ok.id)
            self.assertEqual(ok.state, 'done')
            self.assertEqual(ok.book_quantity, 4000.0)
            self.assertEqual(ok.variance, -20.0)
            self.assertAlmostEqual(ok.variance_percent, -0.5)
            self.assertIsNone(ok.move)
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 4000.0)
            big, = Dip.create([dict(
                        values, measured_quantity=3500.0, adjust=True)])
            with self.assertRaises(FuelWarning):
                Dip.validate_dip([big])
            with Transaction().set_context(_skip_warnings=True):
                Dip.validate_dip([Dip(big.id)])
            big = Dip(big.id)
            self.assertEqual(big.move.state, 'done')
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 3500.0)
            gain, = Dip.create([dict(
                        values, measured_quantity=3510.0, adjust=True)])
            Dip.validate_dip([gain])
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 3510.0)
            with self.assertRaises(FuelError):
                Dip.delete([Dip(gain.id)])

    @with_transaction()
    def test_sales_report(self):
        "Test the sales analysis only counts closed shifts"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Report = pool.get('fuel.report.sales')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._shift(company, ctx)
            Shift.open_shift([shift])
            self.assertEqual(Report.search([]), [])
            for line in Shift(shift.id).lines:
                line.closing_index = line.opening_index + 100
                line.save()
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([Shift(shift.id)])
            rows = Report.search([])
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(r.quantity for r in rows), 200.0)
            self.assertEqual(
                sum(r.amount for r in rows), Decimal('550.00'))

    @with_transaction()
    def test_shift_report_renders(self):
        "Test the shift report template renders"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            ShiftReport = pool.get('fuel.shift.report', type='report')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._shift(company, ctx)
            Shift.open_shift([shift])
            for line in Shift(shift.id).lines:
                line.closing_index = line.opening_index + 10
                line.save()
            Shift.write([Shift(shift.id)], {'payments': [('create', [
                            {'method': 'cash', 'amount': Decimal('55')}])]})
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([Shift(shift.id)])
            oext, content, _, _ = ShiftReport.execute([shift.id], {})
            self.assertEqual(oext, 'odt')
            self.assertTrue(content)

    def _open_shift(self, company, ctx):
        Shift = Pool().get('fuel.shift')
        shift = self._shift(company, ctx)
        Shift.open_shift([shift])
        return Shift(shift.id)

    @with_transaction()
    def test_alerts(self):
        "Low tanks and long shifts are notified once"
        import types
        from unittest.mock import patch
        from .. import alert
        company = create_company()
        with set_company(company):
            pool = Pool()
            Station = pool.get('fuel.station')
            Tank = pool.get('fuel.tank')
            Shift = pool.get('fuel.shift')
            ctx = self._setup(company)
            with patch('trytond.bus.notify') as notify:
                self.assertEqual(Station.notify_alerts(), 2)  # 2 empty tanks
                self.assertTrue(notify.called)
                notify.reset_mock()
                self.assertEqual(Station.notify_alerts(), 0)  # once
                self.assertFalse(notify.called)
                self._fill(company, ctx, ctx['tank1'], 5000.0)
                Station.notify_alerts()
                self.assertFalse(Tank(ctx['tank1'].id).low_notified)
                self.assertTrue(Tank(ctx['tank2'].id).low_notified)
                shift = self._open_shift(company, ctx)
                self.assertEqual(Shift.notify_long_open(), 0)
                real = datetime.datetime

                class Later(real):
                    @classmethod
                    def now(cls, tz=None):
                        return real.now() + datetime.timedelta(hours=13)
                fake = types.SimpleNamespace(
                    datetime=Later, timedelta=datetime.timedelta)
                with patch.object(alert, 'datetime', fake):
                    self.assertEqual(Shift.notify_long_open(), 1)
                    self.assertEqual(Shift.notify_long_open(), 0)
                self.assertTrue(Shift(shift.id).long_notified)

    @with_transaction()
    def test_party_first_name(self):
        "Test the first name of a natural person"
        pool = Pool()
        Party = pool.get('party.party')
        person, company_party = Party.create([
                {'name': "Pierre", 'is_person': True, 'first_name': "Jean"},
                {'name': "ACME", 'is_person': False, 'first_name': "X"}])
        self.assertEqual(person.rec_name, "Jean Pierre")
        self.assertEqual(company_party.rec_name, "ACME")
        self.assertEqual(Party.search([('rec_name', '=', "Jean")]), [person])
        person.is_person = False
        person.on_change_is_person()
        self.assertIsNone(person.first_name)

    @with_transaction()
    def test_gallon_default(self):
        "Test fuels are measured in gallons"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Template = pool.get('product.template')
            Uom = pool.get('product.uom')
            template = Template(fuel=True)
            template.on_change_fuel()
            self.assertEqual(template.default_uom.symbol, 'gal')
            unit, = Uom.search([('name', '=', "Unit")])
            with self.assertRaises(FuelError):
                Template.create([{
                            'name': "Bad", 'type': 'goods', 'fuel': True,
                            'default_uom': unit.id,
                            'list_price': Decimal('1')}])

    @with_transaction()
    def test_temperature_correction(self):
        "Test volume correction factor"
        from ..tools import volume_correction_factor as vcf
        self.assertEqual(vcf('diesel', 840, None), 1.0)
        self.assertEqual(vcf('diesel', 840, 15.0), 1.0)
        self.assertLess(vcf('diesel', 840, 30.0), 1.0)
        self.assertGreater(vcf('diesel', 840, 5.0), 1.0)
        self.assertAlmostEqual(vcf('diesel', 840, 30.0), 0.9871, places=3)
        self.assertEqual(vcf('other', 840, 30.0), 1.0)

    @with_transaction()
    def test_delivery_booked_at_15(self):
        "Test the delivery is booked at 15 degC when the station asks"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Delivery = pool.get('fuel.delivery')
            Tank = pool.get('fuel.tank')
            ctx = self._setup(company)
            station, tank = ctx['station'], ctx['tank1']
            template = tank.product.template
            template.fuel_family = 'diesel'
            template.fuel_density = 840.0
            template.save()
            station.temperature_correction = True
            station.save()
            delivery, = Delivery.create([{
                        'company': company.id, 'station': station.id,
                        'supplier': ctx['supplier'].id, 'date': TODAY,
                        'lines': [('create', [{
                                        'tank': tank.id, 'quantity': 1000.0,
                                        'temperature': 30.0,
                                        'unit_cost': Decimal('1')}])]}])
            line, = delivery.lines
            self.assertLess(line.net_quantity, 1000.0)
            Delivery.receive([delivery])
            net = Tank(tank.id).quantity
            self.assertAlmostEqual(net, line.net_quantity, places=2)
            self.assertLess(net, 1000.0)

    @with_transaction()
    def test_credit_needs_customer(self):
        "Test credit payments must name the customer"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Payment = pool.get('fuel.shift.payment')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._open_shift(company, ctx)
            for line in shift.lines:
                line.closing_index = line.opening_index + 10
                line.save()
            payment, = Payment.create([{
                        'shift': shift.id, 'method': 'credit',
                        'amount': Decimal('55'),
                        'party': ctx['supplier'].id}])
            self.assertEqual(payment.party, ctx['supplier'])
            payment.party = None
            with self.assertRaises(ValidationError):
                payment.save()

    @with_transaction()
    def test_shop_sales(self):
        "Test shop and LPG sales at the shift move the stock"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Template = pool.get('product.template')
            Uom = pool.get('product.uom')
            Move = pool.get('stock.move')
            Location = pool.get('stock.location')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            unit, = Uom.search([('name', '=', "Unit")])
            template, = Template.create([{
                        'name': "LPG Cylinder", 'type': 'goods',
                        'default_uom': unit.id, 'list_price': Decimal('25'),
                        'products': [('create', [
                                    {'cost_price': Decimal('18')}])]}])
            cylinder = template.products[0]
            shift = self._open_shift(company, ctx)
            for line in shift.lines:
                line.closing_index = line.opening_index + 10
                line.save()
            Shift.write([shift], {'shop_sales': [('create', [
                            {'product': cylinder.id, 'quantity': 2.0,
                                'unit_price': Decimal('25')}])]})
            shift = Shift(shift.id)
            sale, = shift.shop_sales
            self.assertEqual(sale.amount, Decimal('50'))
            self.assertEqual(shift.shop_total, Decimal('50'))
            self.assertEqual(shift.total_sales, shift.fuel_sales + 50)
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([shift])
            sale = Shift(shift.id).shop_sales[0]
            self.assertTrue(sale.move)
            self.assertEqual(sale.move.state, 'done')
            self.assertEqual(
                sale.move.from_location,
                ctx['station'].warehouse.storage_location)
            with self.assertRaises(FuelError):
                sale.quantity = 3.0
                sale.save()

    @with_transaction()
    def test_meter_readings(self):
        "Test automatic meter readings fill the closing indexes"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Reading = pool.get('fuel.meter.reading')
            ctx = self._setup(company)
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
            shift = self._open_shift(company, ctx)
            with self.assertRaises(FuelError):
                Reading.record('S1', 'P1', 99, 10.0)
            Reading.record('S1', 'P1', 1, 1100.0)
            Reading.record('S1', 'P1', 1, 1150.5)
            Reading.record('S1', 'P1', 2, '5200.25')
            Shift.fetch_readings([shift])
            line1, line2 = Shift(shift.id).lines
            self.assertEqual(line1.closing_index, 1150.5)
            self.assertEqual(line2.closing_index, 5200.25)
            self.assertEqual(line1.quantity, 150.5)
            Reading.record('S1', 'P1', 1, 10.0)  # lower than opening
            with self.assertRaises(FuelError):
                Shift.fetch_readings([Shift(shift.id)])

    # -- authorizations -------------------------------------------------

    def _auth_setup(self, company, fill=True):
        pool = Pool()
        Station = pool.get('fuel.station')
        User = pool.get('res.user')
        ctx = self._setup(company)
        if fill:
            self._fill(company, ctx, ctx['tank1'], 5000.0)
            self._fill(company, ctx, ctx['tank2'], 5000.0)
        station = ctx['station']
        station.authorization_control = True
        station.save()
        with Transaction().set_user(0):
            Station.heartbeat('S1', company.id)
        requester, = User.create([{'name': "Requester", 'login': "req"}])
        ctx['requester'] = requester
        ctx['station'] = Station(station.id)
        return ctx

    def _request(self, company, ctx, **values):
        Authorization = Pool().get('fuel.authorization')
        vals = {
            'company': company.id, 'station': ctx['station'].id,
            'pump': ctx['n1'].pump.id, 'nozzle': ctx['n1'].id,
            'limit_type': 'quantity', 'max_quantity': 20.0,
            'vehicle': "AA-1234"}
        vals.update(values)
        with Transaction().set_user(ctx['requester'].id):
            auth, = Authorization.create([vals])
        return auth

    @with_transaction()
    def test_authorization_flow(self):
        "Test request, approval, middleware exchange and booking"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Authorization = pool.get('fuel.authorization')
            Tank = pool.get('fuel.tank')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            self.assertEqual(auth.state, 'pending')
            self.assertTrue(auth.number.startswith('AU/'))
            self.assertEqual(auth.unit_price, Decimal('2.50'))
            self.assertEqual(auth.max_amount, Decimal('50.00'))
            self.assertEqual(auth.requested_by, ctx['requester'])
            # Nothing is sent before the approval
            with Transaction().set_user(0):
                self.assertEqual(
                    Authorization.middleware_pending('S1', company.id), [])
            Authorization.approve([auth])
            auth = Authorization(auth.id)
            self.assertEqual(auth.state, 'approved')
            self.assertEqual(auth.approved_by.id, 1)
            self.assertTrue(auth.expires_at)
            with Transaction().set_user(0):
                pending = Authorization.middleware_pending('S1', company.id)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]['number'], auth.number)
                self.assertEqual(pending[0]['pump'], "P1")
                self.assertEqual(pending[0]['max_quantity'], 20.0)
                Authorization.middleware_transmitted(auth.number)
                self.assertEqual(
                    Authorization.middleware_pending('S1', company.id), [])
                Authorization.middleware_confirmed(auth.number, "CTRL-9")
                ok = Authorization.middleware_result(
                    auth.number, 18.5, '46.25', "TX-1")
            self.assertTrue(ok)
            auth = Authorization(auth.id)
            self.assertEqual(auth.state, 'completed')
            self.assertEqual(auth.dispensed_quantity, 18.5)
            self.assertEqual(auth.move.state, 'done')
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 5000.0 - 18.5)
            events = [l.event for l in auth.logs]
            self.assertEqual(events, [
                    'requested', 'approved', 'transmitted', 'confirmed',
                    'completed'])

    @with_transaction()
    def test_authorization_amount_limit(self):
        "Test the quantity is derived from a maximum amount"
        company = create_company()
        with set_company(company):
            ctx = self._auth_setup(company)
            auth = self._request(
                company, ctx, limit_type='amount', max_amount=Decimal('100'),
                max_quantity=None)
            self.assertEqual(auth.max_quantity, 40.0)
            auth = self._request(
                company, ctx, nozzle=ctx['n2'].id, limit_type='amount',
                max_amount=Decimal('10'), max_quantity=None)
            self.assertEqual(auth.max_quantity, 3.333)

    @with_transaction()
    def test_authorization_segregation(self):
        "Test the requester cannot approve and only the middleware talks"
        company = create_company()
        with set_company(company):
            Authorization = Pool().get('fuel.authorization')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            with Transaction().set_user(ctx['requester'].id):
                with self.assertRaises(FuelError):
                    Authorization.approve([auth])  # not a manager
            auth2 = Authorization.create([{
                        'company': company.id,
                        'station': ctx['station'].id,
                        'pump': ctx['n1'].pump.id, 'nozzle': ctx['n1'].id,
                        'limit_type': 'quantity', 'max_quantity': 5.0}])[0]
            with self.assertRaises(FuelError):
                Authorization.approve([auth2])  # same user, user 1
            Authorization.approve([auth])
            with self.assertRaises(FuelError):
                Authorization.middleware_pending('S1', company.id)  # user 1

    @with_transaction()
    def test_authorization_communication_lost(self):
        "Test nothing is authorized when the communication is lost"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Authorization = pool.get('fuel.authorization')
            Station = pool.get('fuel.station')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            station = ctx['station']
            station.last_heartbeat = (
                datetime.datetime.now() - datetime.timedelta(minutes=10))
            station.save()
            self.assertFalse(Station(station.id).communication_ok)
            with self.assertRaises(FuelError):
                Authorization.approve([auth])
            with self.assertRaises(FuelError):
                self._request(company, ctx)
            with Transaction().set_user(0):
                Station.heartbeat('S1', company.id)
            Authorization.approve([Authorization(auth.id)])
            station = Station(station.id)
            station.communication_blocked = True
            station.save()
            with Transaction().set_user(0):
                with self.assertRaises(FuelError):
                    Authorization.middleware_transmitted(auth.number)
            with self.assertRaises(FuelError):
                self._request(company, ctx)

    @with_transaction()
    def test_authorization_not_enabled(self):
        "Test a station without control refuses authorizations"
        company = create_company()
        with set_company(company):
            ctx = self._auth_setup(company)
            station = ctx['station']
            station.authorization_control = False
            station.save()
            with self.assertRaises(FuelError):
                self._request(company, ctx)

    @with_transaction()
    def test_authorization_discrepancy(self):
        "Test an excess or a second result is reported, not booked"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Authorization = pool.get('fuel.authorization')
            Tank = pool.get('fuel.tank')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            Authorization.approve([auth])
            with Transaction().set_user(0):
                Authorization.middleware_transmitted(auth.number)
                Authorization.middleware_confirmed(auth.number)
                self.assertFalse(Authorization.middleware_result(
                        auth.number, 25.0, '62.50', "TX-2"))
            auth = Authorization(auth.id)
            self.assertEqual(auth.state, 'discrepancy')
            self.assertIsNone(auth.move)
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 5000.0)
            self.assertIn('exceeds', auth.logs[-1].note)
            Authorization.resolve([auth])
            self.assertEqual(Authorization(auth.id).state, 'resolved')
            # Single use: a second result is refused and logged
            auth2 = self._request(company, ctx)
            Authorization.approve([auth2])
            with Transaction().set_user(0):
                Authorization.middleware_transmitted(auth2.number)
                Authorization.middleware_confirmed(auth2.number)
                self.assertTrue(Authorization.middleware_result(
                        auth2.number, 10.0, '25', "TX-3"))
                self.assertFalse(Authorization.middleware_result(
                        auth2.number, 10.0, '25', "TX-3b"))
                with self.assertRaises(FuelError):
                    Authorization.middleware_result(
                        auth.number, 1.0, '2.5', "TX-3")
            self.assertEqual(Authorization(auth2.id).state, 'completed')
            self.assertEqual(
                Tank(ctx['tank1'].id).quantity, 5000.0 - 10.0)

    @with_transaction()
    def test_authorization_expiry_and_pump_busy(self):
        "Test expiry, late confirmation and one live authorization per pump"
        company = create_company()
        with set_company(company):
            Authorization = Pool().get('fuel.authorization')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            auth2 = self._request(company, ctx)
            Authorization.approve([auth])
            with self.assertRaises(FuelError):
                Authorization.approve([auth2])  # pump busy
            Authorization.write([Authorization(auth.id)], {})
            with Transaction().set_user(0):
                Authorization._set(
                    Authorization(auth.id), 'approved',
                    expires_at=datetime.datetime.now()
                    - datetime.timedelta(minutes=1))
                with self.assertRaises(FuelError):
                    Authorization.middleware_transmitted(auth.number)
            self.assertEqual(Authorization(auth.id).state, 'expired')
            with Transaction().set_user(0):
                self.assertFalse(
                    Authorization.middleware_confirmed(auth.number))
            self.assertEqual(Authorization(auth.id).state, 'discrepancy')
            Authorization.approve([Authorization(auth2.id)])  # pump free

    @with_transaction()
    def test_authorization_cancellation(self):
        "Test the controlled cancellation"
        company = create_company()
        with set_company(company):
            Authorization = Pool().get('fuel.authorization')
            ctx = self._auth_setup(company)
            auth = self._request(company, ctx)
            with Transaction().set_user(ctx['requester'].id):
                Authorization.cancel([auth])
            self.assertEqual(Authorization(auth.id).state, 'cancelled')
            auth = self._request(company, ctx)
            Authorization.approve([auth])
            with Transaction().set_user(0):
                Authorization.middleware_transmitted(auth.number)
            Authorization.cancel([Authorization(auth.id)])
            self.assertEqual(
                Authorization(auth.id).state, 'cancel_requested')
            with Transaction().set_user(0):
                Authorization.middleware_cancel_ack(auth.number)
            auth = Authorization(auth.id)
            self.assertEqual(auth.state, 'cancelled')
            with self.assertRaises(FuelError):
                Authorization.delete([auth])
            with self.assertRaises(FuelError):
                auth.logs[0].__class__.delete([auth.logs[0]])
            with self.assertRaises(FuelError):
                Authorization.write([auth], {'max_quantity': 99.0})

    @with_transaction()
    def test_authorization_and_shift_stock(self):
        "Test authorized gallons are not moved twice at the shift closing"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Authorization = pool.get('fuel.authorization')
            Shift = pool.get('fuel.shift')
            Tank = pool.get('fuel.tank')
            ctx = self._auth_setup(company)
            shift = self._open_shift(company, ctx)
            auth = self._request(company, ctx)
            Authorization.approve([auth])
            with Transaction().set_user(0):
                Authorization.middleware_transmitted(auth.number)
                Authorization.middleware_confirmed(auth.number)
                Authorization.middleware_result(
                    auth.number, 10.0, '25', "TX-5")
            auth = Authorization(auth.id)
            self.assertEqual(auth.shift, shift)
            line1, line2 = Shift(shift.id).lines
            line1.closing_index = line1.opening_index + 30
            line1.save()
            line2.closing_index = line2.opening_index
            line2.save()
            shift = Shift(shift.id)
            self.assertEqual(shift.authorized_total, Decimal('25'))
            Shift.write([shift], {'payments': [('create', [
                            {'method': 'cash', 'amount': Decimal('50')}])]})
            shift = Shift(shift.id)
            self.assertEqual(shift.total_sales, Decimal('75.00'))
            self.assertEqual(shift.difference, Decimal('0'))
            Shift.close([shift])
            self.assertEqual(Tank(ctx['tank1'].id).quantity, 5000.0 - 30.0)

    @with_transaction()
    def test_usage_guide(self):
        "Test the guide wizard content"
        from trytond.i18n import gettext
        text = gettext('fuel_station.msg_guide_usage')
        self.assertIn("AUTHORIZATIONS", text)
        Guide = Pool().get('fuel.guide', type='wizard')
        session_id, _, _ = Guide.create()
        self.assertIn('content', Guide(session_id).default_start([]))


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
            FuelStationTestCase))
    return suite
