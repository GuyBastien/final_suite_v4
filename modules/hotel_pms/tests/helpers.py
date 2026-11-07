# This file is part of the Tryton hotel_pms module.
"Test helpers shared by the modules of the hotel suite"
import datetime
from decimal import Decimal

from trytond.modules.account.tests import create_chart
from trytond.modules.hotel.tests.test_module import (
    create_hotel, create_room_type)
from trytond.pool import Pool
from trytond.transaction import Transaction

START = datetime.datetime(2030, 1, 7, 14, 0)    # Monday
END = datetime.datetime(2030, 1, 9, 11, 0)      # 2 nights


def category():
    "Accounting category (the chart is created on the first call)"
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
    cat, = Category.create([{
                'name': "Hotel", 'accounting': True,
                'account_revenue': revenue.id,
                'account_expense': expense.id}])
    return cat.id


def service(name, price):
    pool = Pool()
    Template = pool.get('product.template')
    Uom = pool.get('product.uom')
    unit, = Uom.search([('name', '=', "Unit")])
    template, = Template.create([{
                'name': name, 'type': 'service',
                'account_category': category(),
                'default_uom': unit.id, 'sale_uom': unit.id,
                'salable': True, 'list_price': Decimal(price),
                'products': [('create', [{}])],
                }])
    return template.products[0]


def setup_hotel(company, rooms=('101', '102')):
    "Return hotel, room_type, rooms, party"
    pool = Pool()
    Room = pool.get('hotel.room')
    Party = pool.get('party.party')
    hotel = create_hotel(company)
    room_type = create_room_type(hotel)
    room_type.night_product = service("Night", '100')
    room_type.hour_product = service("Hour", '20')
    room_type.save()
    rooms = Room.create([{
                'hotel': hotel.id, 'name': n, 'type': room_type.id}
            for n in rooms])
    party, = Party.create([{'name': "Guest"}])
    return hotel, room_type, rooms, party


def make_reservation(company, hotel, party, room_type, room,
        start=START, end=END, **values):
    Reservation = Pool().get('hotel.reservation')
    values.update({
            'company': company.id, 'hotel': hotel.id,
            'party': party.id, 'start': start, 'end': end,
            'lines': [('create', [{
                            'room_type': room_type.id,
                            'room': room.id if room else None}])],
            })
    return Reservation.create([values])[0]
