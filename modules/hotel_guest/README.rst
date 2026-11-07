Hotel Guest
###########

Guest management on top of ``hotel`` and ``hotel_pms``.

* Guest profile on the party: nationality, birth date, identity document,
  preferences, VIP, blacklist, GDPR and marketing consents, number of stays.
* A blacklisted guest cannot be confirmed in a reservation.
* Loyalty program per company (points per currency unit, silver and gold
  thresholds); points are earned once at check-out; manual adjustments in
  *Guests > Loyalty Entries*.
* Guest registration cards on the reservation. When the hotel requires
  registration, check-in needs one card per adult and child.
* GDPR: the *Anonymize* button of the party erases the personal data (refused
  while the guest has open reservations).
* Key cards (issue, revoke, return); the cards of a reservation are revoked at
  check-out. ``Keycard._provider_issue`` and ``_provider_revoke`` are empty
  hooks to extend with a door lock vendor integration.
