# This file is part of the Tryton sale_point_cash_control module.
import unittest
from decimal import Decimal

from trytond.modules.company.tests import (
    create_company, set_company)
from trytond.pool import Pool
from trytond.tests import test_tryton
from trytond.tests.test_tryton import with_transaction
from trytond.exceptions import UserWarning

from trytond.modules.account.tests import create_chart, get_fiscalyear

from ..exceptions import CashControlError, CashControlWarning


def create_point(company, cash_control=True):
    pool = Pool()
    Location = pool.get('stock.location')
    Journal = pool.get('account.journal')
    Sequence = pool.get('ir.sequence.strict')
    SequenceType = pool.get('ir.sequence.type')
    Point = pool.get('sale.point')
    ModelData = pool.get('ir.model.data')
    storage, customer = Location.create([
            {'name': "Storage", 'type': 'storage'},
            {'name': "Customer", 'type': 'customer'}])
    journal, = Journal.create([
            {'name': "Revenue", 'code': 'REV', 'type': 'revenue'}])
    seq_type = SequenceType(ModelData.get_id('sale_point', 'sequence_type_sale'))
    sequence, = Sequence.create([{
                'name': "POS", 'sequence_type': seq_type.id,
                'company': company.id}])
    point, = Point.create([{
                'name': "Counter", 'company': company.id,
                'storage_location': storage.id,
                'customer_location': customer.id, 'journal': journal.id,
                'sequence': sequence.id, 'cash_control': cash_control}])
    return point


def create_transfer_type(company):
    pool = Pool()
    Type = pool.get('sale.point.cash.transfer.type')
    Journal = pool.get('account.journal')
    Account = pool.get('account.account')
    create_chart(company)
    journal, = Journal.create([
            {'name': "Difference", 'code': 'DIF', 'type': 'cash'}])
    account, = Account.search([
            ('type.expense', '=', True), ('company', '=', company.id)],
        limit=1)
    cash_account, = Account.search([
            ('type.expense', '=', False), ('closed', '!=', True),
            ('type', '!=', None), ('company', '=', company.id),
            ('name', 'ilike', '%Cash%')], limit=1)
    Method = pool.get('sale.point.payment.method')
    Method.create([{'company': company.id, 'name': "Cash", 'cash': True,
                'account': cash_account.id}])
    type_, = Type.create([{
                'company': company.id, 'name': "Cash Difference",
                'journal': journal.id, 'account': account.id}])
    return type_


class SalePointCashControlTestCase(test_tryton.ModuleTestCase):
    "Test sale_point_cash_control module"
    module = 'sale_point_cash_control'

    @with_transaction()
    def test_standard_denominations(self):
        "Standard banknotes and coins are created once"
        company = create_company()
        with set_company(company):
            Denomination = Pool().get('sale.point.denomination')
            created = Denomination.create_standard(company)
            # USD from create_company currency
            self.assertTrue(created)
            self.assertEqual(Denomination.create_standard(company), [])
            first = Denomination.search([])[0]
            self.assertEqual(first.value, Decimal('100'))
            self.assertIn('USD', first.rec_name.upper())

    @with_transaction()
    def test_count_prefill_and_close(self):
        "Count lines are prefilled and drive the end amount"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            session = Session.get_current(point)
            n = len(Denomination.search([]))
            self.assertEqual(len(session.opening_counts), n)
            self.assertEqual(len(session.closing_counts), n)
            # count 3 x 20 and 2 x 5 on closing
            lines = {c.denomination.value: c for c in session.closing_counts
                if c.denomination.kind == 'bill'}
            Count.write([lines[Decimal('20')]], {'quantity': 3})
            Count.write([lines[Decimal('5')]], {'quantity': 2})
            session = Session(session.id)
            self.assertEqual(session.closing_counted, Decimal('70.00'))
            self.assertEqual(session.expected_amount, Decimal('0'))
            self.assertEqual(session.closing_difference, Decimal('70.00'))
            # difference without type -> error
            with self.assertRaises(CashControlError):
                Session.record_difference([session])
            point.difference_type = create_transfer_type(company)
            point.save()
            Session.record_difference([Session(session.id)])
            session = Session(session.id)
            self.assertEqual(len(session.transfers), 1)
            self.assertEqual(session.transfers[0].amount, Decimal('70.00'))
            self.assertEqual(session.closing_difference, Decimal('0'))
            Session.close([session])
            self.assertEqual(Session(session.id).state, 'closed')
            self.assertEqual(Session(session.id).end_amount, Decimal('70.00'))

    @with_transaction()
    def test_close_requires_count(self):
        "Closing without count is refused, with matching count works"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            session = Session.get_current(point)
            # expected 0 and counted 0 is accepted
            Session.close([session])
            self.assertEqual(Session(session.id).state, 'closed')

    @with_transaction()
    def test_opening_carry_over(self):
        "Next session copies the closing count and warns on a gap"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            point.difference_type = create_transfer_type(company)
            point.save()
            fiscalyear = get_fiscalyear(company)
            fiscalyear.save()
            pool.get('account.fiscalyear').create_period([fiscalyear])
            s1 = Session.get_current(point)
            ten = [c for c in s1.closing_counts
                if c.denomination.value == Decimal('10')
                and c.denomination.kind == 'bill'][0]
            Count.write([ten], {'quantity': 4})
            Session.record_difference([Session(s1.id)])
            Session.close([Session(s1.id)])
            Session.post([Session(s1.id)])
            s2 = Session.get_current(point)
            self.assertEqual(s2.start_amount, Decimal('40.00'))
            self.assertEqual(s2.opening_counted, Decimal('40.00'))
            self.assertEqual(s2.opening_difference, Decimal('0'))
            # cashier finds one 10 missing at opening
            ten2 = [c for c in s2.opening_counts
                if c.denomination.value == Decimal('10')
                and c.denomination.kind == 'bill'][0]
            Count.write([ten2], {'quantity': 3})
            s2 = Session(s2.id)
            self.assertEqual(s2.opening_difference, Decimal('-10.00'))
            closing = [c for c in s2.closing_counts
                if c.denomination.value == Decimal('10')
                and c.denomination.kind == 'bill'][0]
            Count.write([closing], {'quantity': 3})
            with self.assertRaises(CashControlWarning):
                Session.close([Session(s2.id)])


    @with_transaction()
    def test_opening_not_counted_is_a_gap(self):
        "Not counting at opening a drawer that holds money is reported"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            point.difference_type = create_transfer_type(company)
            point.save()
            fiscalyear = get_fiscalyear(company)
            fiscalyear.save()
            pool.get('account.fiscalyear').create_period([fiscalyear])
            s1 = Session.get_current(point)
            ten = [c for c in s1.closing_counts
                if c.denomination.value == Decimal('10')
                and c.denomination.kind == 'bill'][0]
            Count.write([ten], {'quantity': 4})
            Session.record_difference([Session(s1.id)])
            Session.close([Session(s1.id)])
            Session.post([Session(s1.id)])
            s2 = Session.get_current(point)
            self.assertEqual(s2.opening_difference, Decimal('0'))
            # the cashier erases the prefilled opening count
            Count.write([c for c in s2.opening_counts], {'quantity': 0})
            s2 = Session(s2.id)
            self.assertEqual(s2.start_amount, Decimal('40.00'))
            self.assertEqual(s2.opening_difference, Decimal('-40.00'))

    @with_transaction()
    def test_no_control(self):
        "A point without cash control keeps the standard behavior"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Denomination.create_standard(company)
            point = create_point(company, cash_control=False)
            session = Session.get_current(point)
            self.assertEqual(len(session.opening_counts), 0)
            Session.close([session])
            self.assertEqual(Session(session.id).state, 'closed')

    @with_transaction()
    def test_count_locked_after_close(self):
        "Counts cannot change once the session is closed"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination = pool.get('sale.point.denomination')
            Denomination.create_standard(company)
            point = create_point(company, cash_control=False)
            session = Session.get_current(point)
            denomination = Denomination.search([])[0]
            line, = Count.create([{
                        'session': session.id, 'moment': 'closing',
                        'denomination': denomination.id, 'quantity': 0}])
            Session.close([session])
            with self.assertRaises(CashControlError):
                Count.write([line], {'quantity': 4})

    @with_transaction()
    def test_suspect_not_counted(self):
        "Suspect banknotes are shown apart and never added to the total"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            session = Session.get_current(point)
            line = [c for c in session.closing_counts
                if c.denomination.kind == 'bill'
                and c.denomination.value == Decimal('20')][0]
            Count.write([line], {'quantity': 3, 'suspect_quantity': 1})
            line = Count(line.id)
            self.assertEqual(line.amount, Decimal('40'))
            self.assertEqual(line.suspect_amount, Decimal('20'))
            session = Session(session.id)
            self.assertEqual(session.closing_counted, Decimal('40.00'))
            self.assertEqual(session.closing_suspect, Decimal('20.00'))
            with self.assertRaises(Exception):
                Count.write([line], {'suspect_quantity': 4})

    @with_transaction()
    def test_close_warns_on_difference(self):
        "Closing with a shortage or overage asks for confirmation"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Denomination.create_standard(company)
            point = create_point(company)
            session = Session.get_current(point)
            line = [c for c in session.closing_counts
                if c.denomination.kind == 'bill'
                and c.denomination.value == Decimal('20')][0]
            Count.write([line], {'quantity': 2})
            self.assertEqual(Session(session.id).closing_difference,
                Decimal('40.00'))
            with self.assertRaises(CashControlWarning):
                Session.close([Session(session.id)])

    @with_transaction()
    def test_close_after_confirmed_difference(self):
        "Confirming a count difference closes the session and books it"
        company = create_company()
        with set_company(company):
            pool = Pool()
            Denomination = pool.get('sale.point.denomination')
            Session = pool.get('sale.point.cash.session')
            Count = pool.get('sale.point.cash.count')
            Transfer = pool.get('sale.point.cash.transfer')
            Warning = pool.get('res.user.warning')
            Denomination.create_standard(company)
            point = create_point(company)
            point.difference_type = create_transfer_type(company)
            point.save()
            session = Session.get_current(point)
            line = [c for c in session.closing_counts
                if c.denomination.kind == 'bill'
                and c.denomination.value == Decimal('20')][0]
            Count.write([line], {'quantity': 2})
            try:
                Session.close([Session(session.id)])
            except CashControlWarning as e:
                Warning.create([{'user': 1, 'name': e.name, 'always': False}])
            Session.close([Session(session.id)])
            session = Session(session.id)
            self.assertEqual(session.state, 'closed')
            self.assertEqual(session.end_amount, Decimal('40.00'))
            self.assertEqual(len(session.transfers), 1)


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            SalePointCashControlTestCase))
    return suite
