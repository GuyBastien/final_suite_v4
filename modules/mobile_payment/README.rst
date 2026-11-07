Mobile Payment
##############

Base module of the mobile money providers (``moncash``, ``natcash``).

* **Operator accounts**: credentials, sandbox/live switch, expiry delay and
  the payment method on which confirmed payments are booked.
* **Transactions**: one per request sent to an operator. States: draft,
  pending, paid, failed, expired, cancelled. Colours in the list: green
  paid, orange pending, red failed/expired/to review, grey cancelled.
* **Point of sale**: button *Pay by Mobile* on the ticket. The payment is
  created only when the operator confirms (never typed by the cashier).
* **Scheduler** *Verify Mobile Payments* (every 2 minutes): verifies pending
  requests, expires the old ones, notifies the cashier (paid, refused,
  expired) and the administrators (payment to review).
* **Return URL**: ``https://<server>/<database>/mobile_payment/<provider>/return``.
  It only triggers a server-side verification.

A payment confirmed for a ticket that is cancelled, already paid or for a
different amount is *not* booked: it is flagged *To Review* so a manager
refunds it or fixes the ticket.

USSD payment and push notifications (7.0.2)
-------------------------------------------

* USSD is *assisted*: the customer dials a code (the account menu code such as
  ``*202#``, or a shortcut with ``{merchant}`` and ``{amount}``), pays, then the
  cashier types the receipt number received by SMS. A receipt pays only once.
  MonCash verifies the receipt (RetrieveTransactionPayment). Otherwise an
  administrator may confirm by hand if the account allows it; the payment is
  then flagged "To Review". No public merchant USSD API exists, none is used.
* Push targets (menu Configuration > Notification Targets): ntfy or JSON
  webhook (https public addresses only), sent through the Tryton queue after
  the commit. Never blocks a payment.
* Not tested against real operator or ntfy servers.
