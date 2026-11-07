Hotel (Tryton 7.0)
==================

Base of the hotel suite (``hotel`` + ``hotel_pms``).

Depends on: company, currency, party, product, stock.

Features
--------
* Hotels linked to a company, a party and a stock warehouse.
* Room types, rooms (a stock location is created for each room), room state
  (available, occupied, dirty, maintenance, out of order).
* Rates per room type with validity period (seasons): night price, weekend
  night price (Fri/Sat nights), hour price, minimum hours, maximum hours
  (beyond it the nightly price applies; an hourly stay never costs more than a
  night). ``RoomType.compute_price(start, end, mode=None)`` returns
  mode/quantity/unit_price/amount.
* Hotel articles and consumables are the native products
  (``product.template``) flagged "Hotel Article", with a hotel category
  (minibar, amenity, F&B, linen, cleaning, maintenance, equipment) and a
  "Billed to Guest" flag. Standard articles per room type (par levels).
* Stock operations (``hotel.consumption``): replenishment (warehouse -> room)
  and consumption (room -> consumption location). Processing generates native
  ``stock.move`` records (origin = operation line), visible in
  Hotel > Stock > Hotel Stock Moves.

Security
--------
Groups "Hotel" and "Hotel Administration". Stock operations create native
stock moves, so hotel users that process operations also need the native
"Stock" group.

Tests
-----
``python -m unittest trytond.modules.hotel.tests``
