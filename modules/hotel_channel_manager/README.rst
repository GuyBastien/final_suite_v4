Hotel Channel Manager
#####################

Calendar (iCal) synchronization of the rooms with online travel agencies
and booking sites.

What it does
------------
* Channels (Hotel > Channel Manager > Channels): one per site, with the
  party used on its reservations and one line per room.
* Import: each room has the iCal address published by the site. Every 30
  minutes (scheduled action "Synchronize Hotel Channels", or the
  "Synchronize" button) the calendar is read: new events create a confirmed
  reservation that blocks the room, moved events replace it, cancelled or
  removed events cancel it.
* Overbooking: an event on a room that is not free is kept as a *Conflict*
  booking, with an alert in the notifications; "Retry" accepts it once the
  room is free.
* Export: each room has a secret address ``/<database>/hotel/ical/<token>.ics``
  (read-only, no guest data, "Reserved" only). Give it, prefixed by the server
  URL, to the sites. A channel never receives back its own bookings, but
  receives those of the others and of the front desk. "New Token" revokes it.
* Failures are logged (Synchronization Logs) and notified.

Limits
------
* This is calendar synchronization only. There is **no** push of rates or
  availability and no API connector (Booking.com, Expedia connectivity
  APIs need a certified partner account); the provider field is informative.
  Whether a site accepts iCal feeds depends on the site and on the account.
* Synchronization is pull-based: up to 30 minutes of delay on import and
  whatever refresh period the site applies on export.
* Times are taken as hotel local time; all-day events use the hotel
  check-in and check-out times.
* The export address is public: whoever has the token can read the dates.
* Imported reservations are priced with the hotel rates, or at 0 if
  "Compute Prices" is off or no rate exists.

Tests: ``python -m unittest trytond.modules.hotel_channel_manager.tests``
