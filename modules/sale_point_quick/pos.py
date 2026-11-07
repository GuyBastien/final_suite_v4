# This file is part of the Tryton sale_point_quick module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.model.exceptions import ValidationError
from trytond.exceptions import UserWarning
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval

_ZERO = Decimal(0)


class PosError(ValidationError):
    pass


class PosWarning(UserWarning):
    pass


class Point(metaclass=PoolMeta):
    __name__ = 'sale.point'

    card_method = fields.Many2One(
        'sale.point.payment.method', "Card Method",
        domain=[
            ('company', '=', Eval('company', -1)),
            ('cash', '=', False)],
        help="Payment method used by the one-click card button.")


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    pos_quick = fields.Boolean(
        "Quick Sale",
        help="Show the product in the quick sale selection of the point "
        "of sale.")


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.point.sale'

    quick_code = fields.Function(
        fields.Char(
            "Quick Code",
            help="Scan or type a product code, optionally preceded by a "
            "quantity: 3*COLA."),
        'get_quick')
    quick_product = fields.Function(
        fields.Many2One(
            'product.product', "Quick Product",
            domain=[('template.pos_quick', '=', True)],
            context={'company': Eval('company', -1)},
            help="Pick a favourite product to add it to the sale."),
        'get_quick')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'pay_cash': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                'pay_card': {
                    'invisible': Eval('state') != 'open',
                    'depends': ['state']},
                })

    @classmethod
    def get_quick(cls, sales, names):
        return {name: {s.id: None for s in sales} for name in names}

    def _quick_line(self, product, quantity):
        Line = Pool().get('sale.point.sale.line')
        line = Line(sale=self, product=product, quantity=quantity)
        line.on_change_product()
        return line

    @fields.depends(
        'quick_code', 'point', 'date', 'company', 'lines',
        methods=['on_change_with_currency'])
    def on_change_quick_code(self):
        Product = Pool().get('product.product')
        code = (self.quick_code or '').strip()
        self.quick_code = None
        if not code or not self.point:
            return
        quantity = 1.0
        for sep in ('*', 'x', 'X'):
            head, found, tail = code.partition(sep)
            if found and head.replace('.', '', 1).isdigit():
                quantity, code = float(head), tail.strip()
                break
        products = Product.search([
                ('code', '=', code), ('template.salable', '=', True)],
            limit=1)
        if not products:
            raise PosError(gettext(
                    'sale_point_quick.msg_unknown_code', code=code))
        self.lines = list(self.lines or []) + [
            self._quick_line(products[0], quantity)]

    @fields.depends(
        'quick_product', 'point', 'date', 'company', 'lines',
        methods=['on_change_with_currency'])
    def on_change_quick_product(self):
        product = self.quick_product
        self.quick_product = None
        if product and self.point:
            self.lines = list(self.lines or []) + [
                self._quick_line(product, 1.0)]

    @classmethod
    def _quick_pay(cls, sales, method_getter):
        pool = Pool()
        Payment = pool.get('sale.point.payment')
        for sale in sales:
            if sale.state != 'open':
                continue
            if not sale.lines:
                raise PosError(gettext(
                        'sale_point_quick.msg_sale_empty', sale=sale.rec_name))
            method = method_getter(sale)
            amount = sale.amount_to_pay
            if amount:
                Payment.create([{
                            'sale': sale.id, 'method': method.id,
                            'amount': amount}])
        sales = cls.browse([s.id for s in sales])
        cls.process([s for s in sales if s.state == 'open'
                and not s.amount_to_pay])

    @classmethod
    @ModelView.button
    def pay_cash(cls, sales):
        "Pay the whole amount in cash with one click"
        Method = Pool().get('sale.point.payment.method')

        def getter(sale):
            methods = Method.search([
                    ('company', '=', sale.company.id), ('cash', '=', True)],
                limit=1)
            if not methods:
                raise PosError(gettext(
                        'sale_point_quick.msg_no_cash_method',
                        company=sale.company.rec_name))
            return methods[0]
        cls._quick_pay(sales, getter)

    @classmethod
    @ModelView.button
    def pay_card(cls, sales):
        "Pay the whole amount by card with one click"

        def getter(sale):
            if not sale.point.card_method:
                raise PosError(gettext(
                        'sale_point_quick.msg_no_card_method',
                        point=sale.point.rec_name))
            return sale.point.card_method
        cls._quick_pay(sales, getter)
