# This file is part of the Tryton fuel_station_sale module.
"""Turn the completed dispensing authorizations into sales and invoices.

The stock has already been moved by fuel_station (tank to customer), so the
sale uses the "manual" shipment method: the module Sale follows the sale and
its invoice, without a second stock move.
"""
from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction, without_check_access


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    @fields.depends(
        'fuel', 'default_uom', 'salable', 'sale_uom',
        methods=['on_change_fuel'])
    def on_change_fuel(self):
        super().on_change_fuel()
        if self.fuel:
            self.salable = True
            self.sale_uom = self.default_uom


class Station(metaclass=PoolMeta):
    __name__ = 'fuel.station'

    sale_party = fields.Many2One(
        'party.party', "Default Customer",
        context={'company': Eval('company', -1)},
        help="Customer of the sales created for authorizations without "
        "customer (walk-in customers).")


class Authorization(metaclass=PoolMeta):
    __name__ = 'fuel.authorization'

    sale = fields.Many2One('sale.sale', "Sale", readonly=True,
        help="Sale generated from this record.")

    @classmethod
    def _after_complete(cls, auth):
        super()._after_complete(auth)
        auth._create_sale()

    def _create_sale(self):
        pool = Pool()
        Sale = pool.get('sale.sale')
        Line = pool.get('sale.line')
        with Transaction().set_context(company=self.company.id):
            party = self.party or self.station.sale_party
            if not party or not self.dispensed_quantity:
                self.__class__._log([self], 'info', gettext(
                        'fuel_station_sale.msg_no_sale'))
                return
            party = party.__class__(party.id)
            product = self.nozzle.tank.product
            sale = Sale(
                company=self.company, party=party,
                currency=self.company.currency,
                warehouse=self.station.warehouse,
                sale_date=pool.get('ir.date').today(),
                shipment_method='manual', invoice_method='order',
                reference=self.number, description=self.vehicle)
            sale.on_change_party()
            sale.shipment_method = 'manual'
            sale.invoice_method = 'order'
            line = Line(
                sale=sale, type='line', product=product,
                quantity=self.dispensed_quantity)
            line.on_change_product()
            line.unit_price = self.unit_price
            line.quantity = self.dispensed_quantity
            sale.lines = [line]
            with without_check_access():
                sale.save()
                Sale.quote([sale])
                Sale.confirm([sale])
                Sale.process([sale])
                super(Authorization, self.__class__).write(
                    [self], {'sale': sale.id})
        self.__class__._log([self.__class__(self.id)], 'info', gettext(
                'fuel_station_sale.msg_sale_created', sale=sale.rec_name))
