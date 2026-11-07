Hotel PMS (Tryton 7.0)
======================

Property management on top of ``hotel``; integrates sale, sale_point,
sale_subscription, sale_discount, purchase (+ purchase_request,
stock_supply), account and account_invoice.

Features
--------
* Reservations (Hotel > Front Desk): rooms per line, availability check,
  hourly or nightly pricing from the hotel rates, per-line discount.
  Workflow: draft, confirmed, checked in (room occupied), checked out
  (room dirty), cancelled.
* Billable consumptions of the room are charged to the stay at check-out.
* Sale: "Create Sale" builds a draft sale (rooms + charges) invoiced with
  the native sale / account_invoice flow; sale and invoice states are shown
  on the reservation.
* Long stays: "Create Subscription" bills the room one night per day with
  the chosen recurrence (needs a daily subscription service on the night
  product).
* Point of sale: payment method "Room Charge"; the ticket must reference a
  checked-in reservation and is settled by the sale of the stay.
* Purchasing: "Update Order Points" on the hotel creates purchase order
  points from the standard articles of the rooms; Hotel > Purchasing.
* Reports (Hotel > Reports): arrivals, departures, occupancy (rate, ADR,
  RevPAR), revenue, and the guest folio (Print on a reservation).
* Dashboard (Hotel > Dashboard): per hotel, room status, occupancy, arrivals
  and departures, guests in house, room revenue, ADR, RevPAR and the open
  notifications. Figures are computed when the view is opened or reloaded
  (Refresh button); there is no automatic polling.
* Notifications (Hotel > Notifications): reminders, alerts, warnings and
  information, raised every 15 minutes by the scheduled action "Generate
  Hotel Notifications" (arrivals, departures, late guests, no-shows, overdue
  check-outs, rooms not ready, unbilled/unpaid stays, low stock, high
  occupancy). They resolve themselves when the condition disappears; manual
  reminders can be created too. New ones are pushed as pop-ups (PostgreSQL
  only). Delays are set on the hotel (Notice Hours, No-show Hours).
* Documentation (Hotel > Documentation): user guide, in English and French.

Configuration checklist
-----------------------
1. Chart of accounts, fiscal year with invoice sequences, payment terms.
2. Hotel (warehouse, consumption location), room types, rooms, rates.
3. Night / hour products: salable services with an account category;
   room type "night product" / "hour product".
4. Long stays: a ``sale.subscription.service`` on the night product with a
   daily consumption recurrence, plus a monthly recurrence rule set.
5. Point of sale: a payment method flagged "Room Charge" and, on the hotel,
   a "Room Charge Product" whose revenue account is the clearing account.
6. Guests need an address to quote the sale.

Translation
-----------
French translation: ``locale/fr.po`` (activate the language, then update the modules).

Tests
-----
``python -m unittest trytond.modules.hotel_pms.tests`` (also run on
PostgreSQL 16).
