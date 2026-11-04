# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What checkout keeps of the gateway payloads, and what a log line may carry of them.

``PaymentIntent.provider_request`` (what checkout sent) and ``provider_notify`` (what the gateway answered) carry
the buyer: name, e-mail, phone, address, IP, the PayPal payer id, Stripe's ``customer_details``. Every gateway
names those keys differently and adds new ones over time, so ``PaymentIntent.save()`` stores them through an
allowlist instead of hunting for them: transaction and order ids, amounts, currencies, statuses and timestamps —
what reconciling a payout with an order needs. Everything else goes, and a container left empty goes with it.
Erasure runs the same scrub over payloads stored before. A key some reader needs goes into ``_KEPT_KEYS`` with its
reason; signatures are verified on the raw request, never on a stored copy.

Keys compare lower-cased without underscores, so ``orderId``, ``order_id`` and ``OrderID`` are one key.
"""

_KEPT_KEYS = frozenset(
    {
        # transaction and order ids (ours and the gateway's)
        "id",
        "orderid",
        "extorderid",
        "externalid",
        "paymentid",
        "sessionid",
        "merchantid",
        "posid",
        "merchantposid",
        "serviceid",
        "remoteid",
        "paymentintent",
        "clientreferenceid",
        "referenceid",
        "methodid",
        "gatewayid",
        # amounts and currencies
        "amount",
        "totalamount",
        "originamount",
        "amounttotal",
        "amountsubtotal",
        "amountdiscount",
        "amountshipping",
        "amounttax",
        "currency",
        "currencycode",
        # statuses
        "status",
        "paymentstatus",
        "intent",
        "eventtype",
        # timestamps
        "created",
        "createtime",
        "updatetime",
        "ordercreatedate",
        "localreceiptdatetime",
        "modifiedat",
    }
)
# PayPal money objects are {"currency_code": ..., "value": ...}; elsewhere a bare "value" can be anything
# (a BLIK code in PayU's payMethod, a tax id in Stripe's customer_details).
_MONEY_VALUE_KEY = "value"


def _normalized(key) -> str:
    return str(key).lower().replace("_", "")


def _kept_keys(node: dict) -> frozenset:
    is_money = any(_normalized(key) == "currencycode" for key in node)
    return (_KEPT_KEYS | {_MONEY_VALUE_KEY}) if is_money else _KEPT_KEYS


def _scrub_dict(node: dict) -> dict:
    kept_keys = _kept_keys(node)
    scrubbed = {}
    for key, value in node.items():
        if isinstance(value, dict | list):
            if kept := scrub_gateway_payload(value):
                scrubbed[key] = kept
        elif _normalized(key) in kept_keys:
            scrubbed[key] = value
    return scrubbed


def scrub_gateway_payload(payload):
    """``payload`` reduced to its ids, amounts, currencies, statuses and timestamps.

    None stays None; a payload that is no JSON object or array (a raw string) cannot be told apart and is dropped.
    """
    if isinstance(payload, dict):
        return _scrub_dict(payload)
    if isinstance(payload, list):
        return [kept for item in payload if isinstance(item, dict | list) and (kept := scrub_gateway_payload(item))]
    return None
