# This file is part of the Tryton hotel_pms module.
from collections import defaultdict

from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    reorder_ratio = fields.Float(
        "Reorder Ratio", digits=(16, 2),
        domain=[('reorder_ratio', '>=', 0), ('reorder_ratio', '<=', 1)],
        help="Fraction of the target stock under which a purchase "
        "request is raised (0.5 reorders when half of the stock is left).")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'update_order_points': {
                    'icon': 'tryton-refresh',
                    },
                })

    @classmethod
    def default_reorder_ratio(cls):
        return 0.5

    def get_article_targets(self):
        """Target stock per product: standard quantity per room x number
        of rooms of each room type, in the default unit of the product."""
        Uom = Pool().get('product.uom')
        targets = defaultdict(float)
        products = {}
        for room_type in self.room_types:
            rooms = len([r for r in room_type.rooms if r.active])
            if not rooms:
                continue
            for article in room_type.articles:
                product = article.product
                quantity = Uom.compute_qty(
                    article.unit, article.quantity * rooms,
                    product.default_uom, round=False)
                targets[product.id] += quantity
                products[product.id] = product
        return [(products[i], q) for i, q in targets.items()]

    def process_order_points(self):
        "Create or update the purchase order points of the hotel articles"
        pool = Pool()
        OrderPoint = pool.get('stock.order_point')
        create, write = [], []
        for product, target in self.get_article_targets():
            minimum = target * self.reorder_ratio
            existing = OrderPoint.search([
                    ('product', '=', product.id),
                    ('warehouse_location', '=', self.warehouse.id),
                    ('type', '=', 'purchase'),
                    ('company', '=', self.company.id),
                    ], limit=1)
            if existing:
                write.extend(([existing[0]], {
                            'min_quantity': minimum,
                            'target_quantity': target}))
            else:
                create.append({
                        'product': product.id,
                        'warehouse_location': self.warehouse.id,
                        'type': 'purchase',
                        'company': self.company.id,
                        'min_quantity': minimum,
                        'target_quantity': target,
                        })
        if create:
            OrderPoint.create(create)
        if write:
            OrderPoint.write(*write)
        return len(create) + len(write) // 2

    @classmethod
    @ModelView.button
    def update_order_points(cls, hotels):
        for hotel in hotels:
            hotel.process_order_points()
