# This file is part of the Tryton fuel_station_invoice module.
from collections import defaultdict
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.modules.fuel_station.exceptions import ShiftError
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction

_ZERO = Decimal(0)


class Shift(metaclass=PoolMeta):
    __name__ = 'fuel.shift'

    invoices = fields.Function(
        fields.One2Many('account.invoice', None, "Invoices",
        help="Invoices generated from this record."),
        'get_invoices')

    def get_invoices(self, name):
        Invoice = Pool().get('account.invoice')
        return [i.id for i in Invoice.search([
                    ('lines.origin', '=', str(self))])]

    @classmethod
    @ModelView.button
    @Workflow.transition('closed')
    def close(cls, shifts):
        super().close(shifts)
        cls.create_invoices(shifts)

    @classmethod
    def create_invoices(cls, shifts):
        "Create one draft customer invoice per customer with credit sales"
        pool = Pool()
        Invoice = pool.get('account.invoice')
        Line = pool.get('account.invoice.line')
        invoices = []
        for shift in shifts:
            credits = defaultdict(list)
            for payment in shift.payments:
                if payment.method == 'credit' and payment.amount:
                    credits[payment.party].append(payment)
            if not credits:
                continue
            product = shift.lines[0].product if shift.lines else None
            account = product.account_revenue_used if product else None
            if not account:
                raise ShiftError(gettext(
                        'fuel_station_invoice.msg_no_revenue_account',
                        product=product.rec_name if product else '',
                        shift=shift.rec_name))
            for party, payments in credits.items():
                with Transaction().set_context(company=shift.company.id):
                    # Reload to get the company dependent accounts
                    party = party.__class__(party.id)
                    receivable = party.account_receivable_used
                address = party.address_get(type='invoice')
                if not address:
                    raise ShiftError(gettext(
                            'fuel_station_invoice.msg_no_invoice_address',
                            party=party.rec_name, shift=shift.rec_name))
                invoice = Invoice(
                    company=shift.company, type='out', party=party,
                    currency=shift.currency, invoice_date=shift.date)
                invoice.invoice_address = address
                invoice.account = receivable
                invoice.payment_term = party.customer_payment_term
                line = Line(
                    invoice=invoice, type='line', quantity=1.0,
                    account=account, origin=shift, unit_price=sum(
                        (p.amount for p in payments), _ZERO),
                    description="%s - %s" % (shift.station.rec_name,
                        shift.number))
                invoice.lines = [line]
                invoices.append(invoice)
        if invoices:
            Invoice.save(invoices)
        return invoices
