import copy
from decimal import Decimal

from django_checkout.enums import PaymentProvider

SECRET_PAYMENT_KEYS = ("card", "authorization_token", "continue_url")
REDACTED = "***REDACTED***"


def payment_entries(order_body: dict | None) -> list[dict]:
    """Payment methods held by an order_body, always as a list.

    ``payment_method`` became a list with the multi-method (voucher) refactor; orders
    written before it still hold a single dict. Callers must never assume either shape.

    CONTRACT: the returned dicts are LIVE REFERENCES into ``order_body`` — the list is
    new, the entries are not. ``redact_payment_secrets`` relies on this to mask in place.
    Never copy or normalize the entries here; doing so would turn redaction into a silent
    no-op and leak payment secrets.
    """
    payment_method = (order_body or {}).get("payment_method")
    if isinstance(payment_method, dict):  # legacy single-object orders
        return [payment_method]
    if isinstance(payment_method, list):
        return [entry for entry in payment_method if isinstance(entry, dict)]
    return []


def _redact_entry(entry: dict) -> None:
    for secret_key in SECRET_PAYMENT_KEYS:
        # only mask what is actually set — masking a null would claim a secret exists
        if entry.get(secret_key) is not None:
            entry[secret_key] = REDACTED

    # pay_code is a credential too (BLIK), but voucher codes are persisted already
    # masked (26WT****) and CMS support reads them to tell which voucher paid.
    # Decided by provenance, never by content: a "contains *" test let any caller keep
    # an arbitrary pay_code out of redaction just by prefixing it with a star.
    if entry.get("pay_code") is not None and not PaymentProvider.is_voucher(code=entry.get("code")):
        entry["pay_code"] = REDACTED


def redact_payment_secrets(order_body: dict | None) -> dict:
    """Strip payment credentials from an order_body before a response.

    These exist in the DB (the payment provider integration needs them at order creation
    time) but must never be exposed through the API — to admins or to customers.
    """
    if not isinstance(order_body, dict):
        return {}
    body = copy.deepcopy(order_body)
    for entry in payment_entries(body):
        _redact_entry(entry)
    return body


def scrub_payment_secrets_in_body(body: dict) -> bool:
    """Strip payment credentials from a body dict IN PLACE (order_body / cart_body).

    The erasure counterpart of ``redact_payment_secrets``: same rule, but it rewrites the
    stored blob instead of a response copy. Anonymization used to cover addresses and gift
    personalization only, so a gateway token outlived the erasure request that was supposed
    to remove the customer.

    Masked voucher codes survive here for the same reason they survive redaction — they are
    not a credential, and support reads them to tell which voucher paid.

    Returns True if there was a payment entry to scrub (caller saves).
    """
    if not isinstance(body, dict):
        return False
    entries = payment_entries(body)
    for entry in entries:
        _redact_entry(entry)
    return bool(entries)


def sanitize(obj) -> dict:
    # convert Decimals to strings
    if isinstance(obj, dict):
        for key, elem in obj.items():
            obj[key] = sanitize(elem)
        return obj
    elif isinstance(obj, list):
        for idx, elem in enumerate(obj):
            obj[idx] = sanitize(elem)
        return obj
    elif isinstance(obj, Decimal):
        return str(obj)
    else:
        return obj


def anonymize(field, field_type):
    anonym_word = "x"
    match field_type:
        case "str":
            return field if not field else anonym_word * len(field)
        case "email":
            return field if not field else f"{anonym_word * len(field.split('@')[0])}@example.com"
        case _:
            raise Exception("Unknown field type")


_ADDRESSES_TO_ANONYMIZE = ("billing_address", "shipping_address")
_PII_FIELDS = (("firstname", "str"), ("lastname", "str"), ("email", "email"))
_GIFT_PII_FIELDS = (("recipient_name", "str"), ("sender_name", "str"), ("message", "str"), ("recipient_email", "email"))


def _anonymize_gift_items(body: dict) -> bool:
    """Scrub gift-card personalization PII (items[].voucher_gift) in a body dict.

    The blob is buyer-supplied and opaque — only string values are anonymized.
    Returns True if at least one gift blob was scrubbed.
    """
    nested = body.get("cart") if isinstance(body.get("cart"), dict) else body
    save = False
    for item in nested.get("items") or []:
        gift = item.get("voucher_gift") if isinstance(item, dict) else None
        if not isinstance(gift, dict):
            continue
        for field_name, field_type in _GIFT_PII_FIELDS:
            if isinstance(gift.get(field_name), str):
                gift[field_name] = anonymize(gift[field_name], field_type)
        save = True
    return save


def anonymize_addresses_in_body(body: dict) -> bool:
    """
    Anonymize PII in billing/shipping addresses AND in gift-card personalization
    (items[].voucher_gift) inside a raw body dict (order_body / cart_body).

    Operates directly on the dict — no schema validation, no DTO deserialization.
    Resilient to historical schema drift in unrelated fields (e.g. cart.gratis_rules,
    cart.available_gratis_rules).

    Returns True if at least one address or gift blob was anonymized (caller saves).
    """
    if not isinstance(body, dict):
        return False

    save = False
    addresses = body.get("addresses")
    if isinstance(addresses, dict):
        for addr_key in _ADDRESSES_TO_ANONYMIZE:
            addr = addresses.get(addr_key)
            if not isinstance(addr, dict):
                continue
            for field_name, field_type in _PII_FIELDS:
                if field_name in addr:
                    addr[field_name] = anonymize(addr[field_name], field_type)
            save = True
    return _anonymize_gift_items(body) or save
