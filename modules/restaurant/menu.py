# This file is part of the Tryton restaurant module.
import datetime
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Unique, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval

from .exceptions import RestaurantError

COURSES = [
    ('starter', "Starter"),
    ('main', "Main Course"),
    ('dessert', "Dessert"),
    ('drink', "Drink"),
    ('other', "Other"),
    ]
STATIONS = [
    ('kitchen', "Kitchen"),
    ('bar', "Bar"),
    ('none', "No Preparation"),
    ]
WEEKDAYS = [
    ('all', "Every day"),
    ('0', "Monday"),
    ('1', "Tuesday"),
    ('2', "Wednesday"),
    ('3', "Thursday"),
    ('4', "Friday"),
    ('5', "Saturday"),
    ('6', "Sunday"),
    ]


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'

    restaurant_item = fields.Boolean(
        "Menu Item",
        help="Dish or drink sold in the restaurant and the bar.")
    course = fields.Selection(
        COURSES, "Course",
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Course of the menu in which the item is served.")
    prep_station = fields.Selection(
        STATIONS, "Preparation",
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Where the item is prepared: its ticket appears on this "
        "screen.")
    sold_out = fields.Boolean(
        "Sold Out",
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Temporarily unavailable: it can no longer be ordered.")
    track_stock = fields.Boolean(
        "Deduct from Stock",
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Deduct the item itself from the stock when it is sold "
        "(bottled drinks). Items with a recipe deduct their ingredients.")
    recipe = fields.One2Many(
        'restaurant.recipe', 'template', "Recipe", size=1,
        states={'invisible': ~Eval('restaurant_item', False)},
        help="Ingredients used to prepare the item.")

    @classmethod
    def default_restaurant_item(cls):
        return False

    @classmethod
    def default_course(cls):
        return 'main'

    @classmethod
    def default_prep_station(cls):
        return 'kitchen'

    @classmethod
    def default_track_stock(cls):
        return False

    @classmethod
    def default_sold_out(cls):
        return False

    @fields.depends('restaurant_item', 'course', 'prep_station')
    def on_change_course(self):
        if self.course == 'drink':
            self.prep_station = 'bar'
        elif self.course:
            self.prep_station = 'kitchen'


class Recipe(ModelSQL, ModelView):
    "Menu Item Recipe"
    __name__ = 'restaurant.recipe'

    template = fields.Many2One(
        'product.template', "Menu Item", required=True, ondelete='CASCADE',
        domain=[('restaurant_item', '=', True)],
        help="Product the record applies to.")
    lines = fields.One2Many('restaurant.recipe.line', 'recipe', "Ingredients",
        help="Detail lines of the document.")
    notes = fields.Text("Preparation",
        help="Free text for internal use.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('template_unique', Unique(t, t.template),
                'restaurant.msg_recipe_unique'),
            ]

    def get_rec_name(self, name):
        return self.template.rec_name

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('template.rec_name',) + tuple(clause[1:])]


class RecipeLine(ModelSQL, ModelView):
    "Menu Item Recipe Ingredient"
    __name__ = 'restaurant.recipe.line'

    recipe = fields.Many2One(
        'restaurant.recipe', "Recipe", required=True, ondelete='CASCADE',
        help="Ingredients used to prepare the item.")
    product = fields.Many2One(
        'product.product', "Ingredient", required=True,
        domain=[('template.type', '=', 'goods')],
        help="Product concerned.")
    quantity = fields.Float(
        "Quantity", digits=(16, 3), required=True,
        domain=[('quantity', '>', 0)],
        help="Quantity used for one portion.")
    unit = fields.Function(
        fields.Many2One('product.uom', "Unit",
        help="Unit of measure."), 'on_change_with_unit')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('recipe', 'ASC'))
        cls._order.insert(1, ('id', 'ASC'))

    @fields.depends('product')
    def on_change_with_unit(self, name=None):
        if self.product:
            return self.product.default_uom.id

    @classmethod
    def validate(cls, lines):
        super().validate(lines)
        for line in lines:
            uom = line.product.default_uom
            if uom.round(line.quantity) <= 0:
                raise RestaurantError(gettext(
                        'restaurant.msg_recipe_unit_digits',
                        product=line.product.rec_name, unit=uom.rec_name))


class HappyHour(DeactivableMixin, ModelSQL, ModelView):
    "Happy Hour"
    __name__ = 'restaurant.happy_hour'

    name = fields.Char("Name", required=True, translate=True,
        help="Name displayed in lists and searches.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant",
        help="Leave empty for every restaurant.")
    category = fields.Many2One(
        'product.category', "Category",
        help="Leave empty for every menu item.")
    weekday = fields.Selection(WEEKDAYS, "Day", required=True,
        help="Day of the week the rule applies to.")
    start_hour = fields.Float(
        "From", digits=(4, 2), required=True,
        domain=[('start_hour', '>=', 0), ('start_hour', '<', 24)],
        help="Hour as a decimal: 17.5 means 17:30.")
    end_hour = fields.Float(
        "To", digits=(4, 2), required=True,
        domain=[('end_hour', '>', 0), ('end_hour', '<=', 24)],
        help="End of the period, as a decimal hour (19.5 = 19:30).")
    discount = fields.Numeric(
        "Discount (%)", digits=(5, 2), required=True,
        domain=[('discount', '>', 0), ('discount', '<=', 100)],
        help="Discount rate applied.")

    @classmethod
    def default_weekday(cls):
        return 'all'

    @classmethod
    def default_start_hour(cls):
        return 17.0

    @classmethod
    def default_end_hour(cls):
        return 19.0

    @classmethod
    def validate(cls, rules):
        super().validate(rules)
        for rule in rules:
            if rule.end_hour <= rule.start_hour:
                raise RestaurantError(gettext(
                        'restaurant.msg_happy_hour_hours', rule=rule.rec_name))

    @classmethod
    def get_discount(cls, restaurant, product, when=None):
        "Best discount (%) applicable to the product at the time"
        when = when or datetime.datetime.now()
        hour = when.hour + when.minute / 60.
        categories = {c.id for c in product.template.categories}
        best = Decimal(0)
        for rule in cls.search([]):
            if rule.restaurant and rule.restaurant != restaurant:
                continue
            if rule.weekday != 'all' and int(rule.weekday) != when.weekday():
                continue
            if not (rule.start_hour <= hour < rule.end_hour):
                continue
            if rule.category and rule.category.id not in categories:
                continue
            best = max(best, rule.discount)
        return best


class Menu(DeactivableMixin, ModelSQL, ModelView):
    "Restaurant Menu"
    __name__ = 'restaurant.menu'

    name = fields.Char("Name", required=True, translate=True,
        help="Name displayed in lists and searches.")
    restaurant = fields.Many2One(
        'restaurant.restaurant', "Restaurant", required=True,
        ondelete='CASCADE',
        help="Restaurant concerned.")
    weekday = fields.Selection(WEEKDAYS, "Day", required=True,
        help="Day of the week the rule applies to.")
    start_hour = fields.Float(
        "From", required=True, domain=[
            ('start_hour', '>=', 0), ('start_hour', '<', 24)],
        help="Hour as a decimal: 17.5 means 17:30.")
    end_hour = fields.Float(
        "To", required=True, domain=[
            ('end_hour', '>', 0), ('end_hour', '<=', 24)],
        help="End of the period, as a decimal hour (19.5 = 19:30).")
    items = fields.One2Many('restaurant.menu.item', 'menu', "Items",
        help="Items offered in this menu.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('restaurant', 'ASC'))
        cls._order.insert(1, ('name', 'ASC'))

    @classmethod
    def default_weekday(cls):
        return 'all'

    @classmethod
    def default_start_hour(cls):
        return 0.0

    @classmethod
    def default_end_hour(cls):
        return 24.0

    @classmethod
    def validate(cls, menus):
        super().validate(menus)
        for menu in menus:
            if menu.end_hour <= menu.start_hour:
                raise RestaurantError(gettext(
                        'restaurant.msg_menu_hours', menu=menu.rec_name))

    def is_active_at(self, when):
        hour = when.hour + when.minute / 60.
        if self.weekday != 'all' and int(self.weekday) != when.weekday():
            return False
        return self.start_hour <= hour < self.end_hour

    @classmethod
    def available(cls, restaurant, when=None):
        """Dict {template id: price or None} of the items orderable now.
        Prices are those of the first matching menu that sets one."""
        when = when or datetime.datetime.now()
        result = {}
        for menu in cls.search([('restaurant', '=', restaurant.id)]):
            if not menu.is_active_at(when):
                continue
            for item in menu.items:
                if item.template.id not in result or (
                        result[item.template.id] is None and item.price):
                    result[item.template.id] = item.price
        return result


class MenuItem(ModelSQL, ModelView):
    "Restaurant Menu Item"
    __name__ = 'restaurant.menu.item'

    menu = fields.Many2One(
        'restaurant.menu', "Menu", required=True, ondelete='CASCADE',
        help="Menu card the item belongs to.")
    template = fields.Many2One(
        'product.template', "Item", required=True,
        domain=[('restaurant_item', '=', True)],
        help="Product the record applies to.")
    price = fields.Numeric(
        "Menu Price", digits=(16, 4),
        domain=[['OR', ('price', '=', None), ('price', '>=', 0)]],
        help="Leave empty to keep the list price.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('item_unique', Unique(t, t.menu, t.template),
                'restaurant.msg_menu_item_unique'),
            ]
        cls._order.insert(0, ('menu', 'ASC'))
