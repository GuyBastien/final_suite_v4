Hotel Mobile Payment Module
###########################

Pay a stay (or an advance) with MonCash or NatCash from the reservation.
The operator accounts, the payment dialogue, the verification scheduler
and the notifications are those of ``mobile_payment``.

The confirmed payment becomes a ``Mobile Money`` deposit of the reservation:
``hotel_billing`` already deducts the deposits from the final invoice and
shows them in the balance due, so the folio stays consistent.

* Only confirmed or checked-in stays can be paid.
* A mobile deposit is created by the operator's confirmation, never typed.
* A confirmed payment of another amount, or on a stay that changed state, is
  flagged ``Needs Review`` instead of being booked.

Author: Pierre Michel Augustin. License: GPLv3.
