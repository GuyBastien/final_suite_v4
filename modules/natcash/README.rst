NatCash
#######

NatCash (Natcom Haiti) provider for ``mobile_payment``.

Natcom does not publish a public merchant API. The protocol (signed
requests, checkout page, signed browser callback, transaction check) is
delegated to the open-source SDK **natcashify** (MIT, third party, alpha):

    pip install "natcash-tryton[sdk]"      # installs natcashify, Python >= 3.12

Setup
-----

1. Obtain from Natcom the merchant credentials: partner code, function code,
   username, password and private key (sandbox first).
2. In *Mobile Payment > Configuration > Operator Accounts* create an account,
   provider *NatCash*, fill the credentials and the callback URL
   ``https://<server>/<database>/mobile_payment/natcash/return``.
3. Untick *Sandbox* only when Natcom enabled the live account.

The callback is signed (HMAC) and verified before anything is looked up; the
verdict always comes from a server-to-server transaction check. The secrets
are hidden from non-administrators but stored unencrypted in the database.
Limits: gourdes (HTG) only.
