# This file is part of the Tryton restaurant module.
from trytond.model import DeactivableMixin, ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.pyson import Eval


class Driver(DeactivableMixin, ModelSQL, ModelView):
    "Delivery Driver"
    __name__ = 'restaurant.driver'

    name = fields.Char("Name", required=True,
        help="Name displayed in lists and searches.")
    employee = fields.Many2One(
        'company.employee', "Employee",
        help="Link to an employee if the driver is on the payroll.")
    phone = fields.Char("Phone",
        help="Phone number to call for this record.")
    vehicle = fields.Char("Vehicle", help="Motorbike, car, plate number...")
    in_progress = fields.Function(
        fields.Integer("Deliveries in Progress",
        help="Orders on the way for this driver."), 'get_in_progress')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('name', 'ASC'))

    @classmethod
    def get_in_progress(cls, drivers, name):
        Order = Pool().get('restaurant.order')
        result = {}
        for d in drivers:
            result[d.id] = Order.search_count([
                    ('driver', '=', d.id),
                    ('delivery_state', 'in', ['assigned', 'on_the_way']),
                    ('state', '=', 'open')])
        return result
