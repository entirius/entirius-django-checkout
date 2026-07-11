from decimal import Decimal


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


def anonymize_addresses_in_body(body: dict) -> bool:
    """
    Anonymize PII (firstname/lastname/email) in billing/shipping addresses
    inside a raw body dict (order_body / cart_body).

    Operates directly on the dict — no schema validation, no DTO deserialization.
    Resilient to historical schema drift in unrelated fields (e.g. cart.gratis_rules,
    cart.available_gratis_rules).

    Returns True if at least one address was present (caller should then save).
    """
    if not isinstance(body, dict):
        return False
    addresses = body.get("addresses")
    if not isinstance(addresses, dict):
        return False

    save = False
    for addr_key in _ADDRESSES_TO_ANONYMIZE:
        addr = addresses.get(addr_key)
        if not isinstance(addr, dict):
            continue
        for field_name, field_type in _PII_FIELDS:
            if field_name in addr:
                addr[field_name] = anonymize(addr[field_name], field_type)
        save = True
    return save
