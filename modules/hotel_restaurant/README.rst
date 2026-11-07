Hotel Restaurant Module
#######################

Link between the hotel suite and the restaurant suite, without copying any
of their functions.

* A restaurant belongs to a hotel.
* An order is linked to the checked-in stay of a guest and can be paid with
  the ``Room Charge`` method: the amount becomes a charge of the reservation
  (product: Room Charge Product of the hotel) and is billed with the stay.
  The room charge cannot be typed on a stay that is not checked in, nor
  removed once the stay is billed.
* A table booking can be made for a stay; seating the guests links their
  order to the stay.
* The tables and the table bookings of ``hotel_services`` are replaced by
  those of the restaurant: ``Import Hotel Table Bookings`` (restaurant form)
  brings the open ones once, and the old menu is hidden.

Orders charged to the room are not invoiced by ``restaurant_invoice``: they
are billed on the invoice of the stay.

Author: Pierre Michel Augustin. License: AGPLv3.
