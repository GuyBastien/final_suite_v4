MonCash
#######

MonCash (Digicel Haiti) provider for ``mobile_payment``. It uses the
official MonCash REST API (OAuth2 client credentials, ``CreatePayment``,
``RetrieveOrderPayment``, ``RetrieveTransactionPayment``), the same API
wrapped by the Python SDK *moncashify*.

Setup
-----

1. Create a business account and an application on the MonCash Business
   portal; copy the *Client ID* and the *Secret Key* (sandbox first).
2. In *Mobile Payment > Configuration > Operator Accounts* create an account,
   provider *MonCash*, paste the credentials, choose the payment method and
   press *Test Connection*.
3. In the portal, set the *Return URL* to
   ``https://<server>/<database>/mobile_payment/moncash/return``.
4. Untick *Sandbox* when Digicel activated the live credentials.

Limits: gourdes (HTG) only; the secret key is stored in the database and
hidden from non-administrators, not encrypted.
