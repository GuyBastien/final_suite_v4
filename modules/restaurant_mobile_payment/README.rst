Restaurant Mobile Payment Module
################################

Pay a restaurant order with MonCash or NatCash. The payment dialogue,
the operator accounts, the scheduler that verifies pending requests and the
notifications come from ``mobile_payment``; this module only adds the
``Pay by Mobile`` button on the order and books the confirmed payment as a
``mobile`` payment of the order.

* The payment is recorded only when the operator confirms it; a mobile
  payment cannot be typed by hand.
* A confirmed payment of another amount, or on a closed order, is flagged
  ``Needs Review`` instead of being booked.
* The order is not closed automatically: the waiter closes it, because the
  customer may pay before the last course.

Author: Pierre Michel Augustin. License: GPLv3.
