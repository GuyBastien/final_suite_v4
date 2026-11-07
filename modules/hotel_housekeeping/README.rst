Hotel Housekeeping
##################

Cleaning tasks for the hotel staff, on top of ``hotel`` and ``hotel_pms``.

* Check-out of a reservation creates a "Departure Cleaning" task for each
  room (``high`` priority if a guest arrives soon). The scheduled action "Generate Housekeeping Tasks"
  (every 15 minutes) also creates a task for every dirty room that has none and, if the hotel has
  "Daily Refresh Tasks", a stay-over refresh for the occupied rooms.
* A task has a checklist copied from the hotel checklist (on the hotel form); required items must be done before the task is finished.
* Finishing a cleaning task makes the dirty room available (or waits for
  the supervisor's "Inspect" if the hotel requires inspection).
* Issues (Housekeeping > Issues): a blocking issue puts the room in
  maintenance; resolving it sends the room back to cleaning. Open issues and
  late tasks feed the hotel notifications.
* Groups: "Hotel Housekeeping" (staff: take, start, finish, report issues),
  "Hotel Housekeeping Supervisor" (inspect, redo, cancel).

Mobile
------
Housekeeping > My Tasks uses a compact list and form (room, state, big
buttons, checklist) meant for a phone or tablet through the Tryton web
client (Sao). There is no native mobile application.

Tests: ``python -m unittest trytond.modules.hotel_housekeeping.tests``
