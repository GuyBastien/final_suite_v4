# This file is part of the Tryton fuel_station_cash_control module.
import unittest
from decimal import Decimal

from trytond.model.exceptions import ValidationError
from trytond.modules.company.tests import (
    CompanyTestMixin, create_company, set_company)
from trytond.modules.fuel_station.exceptions import FuelError, FuelWarning
from trytond.modules.fuel_station.tests.test_module import (
    FuelStationTestCase)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction
from trytond.transaction import Transaction


class FuelCashControlTestCase(CompanyTestMixin, test_tryton.ModuleTestCase):
    "Test fuel_station_cash_control module"
    module = 'fuel_station_cash_control'

    # Reuse the fixtures of fuel_station without running its tests
    _setup = FuelStationTestCase._setup
    _fill = FuelStationTestCase._fill
    _shift = FuelStationTestCase._shift
    _open_shift = FuelStationTestCase._open_shift

    def _denominations(self, company):
        Denomination = Pool().get('sale.point.denomination')
        return Denomination.create([
                {'company': company.id, 'kind': 'bill',
                    'value': Decimal('100')},
                {'company': company.id, 'kind': 'coin',
                    'value': Decimal('5')}])

    def _ready(self, company, tolerance='1'):
        ctx = self._setup(company)
        station = ctx['station']
        station.cash_control = True
        station.cash_tolerance = Decimal(tolerance)
        station.save()
        self._fill(company, ctx, ctx['tank1'], 5000.0)
        self._fill(company, ctx, ctx['tank2'], 5000.0)
        return ctx

    def _sell(self, shift, cash, float_=None):
        Shift = Pool().get('fuel.shift')
        for line in shift.lines:
            line.closing_index = line.opening_index + 10
            line.save()
        values = {'payments': [('create', [
                        {'method': 'cash', 'amount': Decimal(cash)}])]}
        if float_ is not None:
            values['opening_float'] = Decimal(float_)
        Shift.write([Shift(shift.id)], values)

    @with_transaction()
    def test_cash_control(self):
        "Count with suspect banknote, then close"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Shift = pool.get('fuel.shift')
            Count = pool.get('fuel.shift.cash.count')
            ctx = self._ready(company)
            shift = self._open_shift(company, ctx)
            with self.assertRaises(FuelError):
                Shift.load_denominations([shift])  # none defined
            note, coin = self._denominations(company)
            Shift.load_denominations([shift])
            Shift.load_denominations([shift])  # idempotent
            shift = Shift(shift.id)
            self.assertEqual(len(shift.cash_counts), 2)
            self._sell(shift, '255', float_='50')
            counts = {c.denomination.id: c for c in shift.cash_counts}
            counts[note.id].quantity = 3
            counts[note.id].suspect_quantity = 1
            counts[note.id].save()
            counts[coin.id].quantity = 21
            counts[coin.id].save()
            shift = Shift(shift.id)
            # 2 good notes (200) + 21 coins (105) = 305; minus float = 255
            self.assertEqual(shift.cash_counted, Decimal('305'))
            self.assertEqual(shift.cash_difference, Decimal('0'))
            self.assertEqual(shift.suspect_total, Decimal('100'))
            with self.assertRaises(FuelWarning):
                Shift.close([shift])  # suspect banknote
            with Transaction().set_context(_skip_warnings=True):
                Shift.close([Shift(shift.id)])
            self.assertEqual(Shift(shift.id).state, 'closed')
            with self.assertRaises(FuelError):
                Count.write([counts[coin.id]], {'quantity': 1})

    @with_transaction()
    def test_suspect_exceeds_counted(self):
        "Suspect quantity cannot exceed the counted quantity"
        company = create_company()
        with set_company(company):
            Count = Pool().get('fuel.shift.cash.count')
            ctx = self._ready(company)
            shift = self._open_shift(company, ctx)
            note, _ = self._denominations(company)
            with self.assertRaises(ValidationError):
                Count.create([{
                            'shift': shift.id, 'denomination': note.id,
                            'quantity': 0, 'suspect_quantity': 2}])

    @with_transaction()
    def test_cash_count_required(self):
        "A shift cannot be closed without count when required"
        company = create_company()
        with set_company(company):
            Shift = Pool().get('fuel.shift')
            ctx = self._ready(company)
            shift = self._open_shift(company, ctx)
            for line in shift.lines:
                line.closing_index = line.opening_index + 1
                line.save()
            with self.assertRaises(FuelError):
                Shift.close([Shift(shift.id)])

    @with_transaction()
    def test_cash_difference_warning(self):
        "A cash difference asks for confirmation"
        company = create_company()
        with set_company(company):
            Shift = Pool().get('fuel.shift')
            ctx = self._ready(company)
            note, _ = self._denominations(company)
            shift = self._open_shift(company, ctx)
            Shift.load_denominations([shift])
            self._sell(shift, '55')
            count = [c for c in Shift(shift.id).cash_counts
                if c.denomination == note][0]
            count.quantity = 1  # 100 counted for 55 expected
            count.save()
            with self.assertRaises(FuelWarning):
                Shift.close([Shift(shift.id)])

    @with_transaction()
    def test_denomination_other_company(self):
        "A denomination of another company is refused"
        company = create_company()
        other = create_company('Other')
        with set_company(other):
            note, _ = self._denominations(other)
        with set_company(company):
            Count = Pool().get('fuel.shift.cash.count')
            ctx = self._ready(company)
            shift = self._open_shift(company, ctx)
            with self.assertRaises(ValidationError):
                Count.create([{
                            'shift': shift.id, 'denomination': note.id,
                            'quantity': 1, 'suspect_quantity': 0}])


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            FuelCashControlTestCase))
    return suite
