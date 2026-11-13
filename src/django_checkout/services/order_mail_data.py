# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The data objects of the order e-mails, read from ``order_body``.

The body is a legacy free-form blob: a missing key or an unparseable amount gives an empty block, never an exception
that would lose the e-mail. Imported only where django_email is installed.
"""

from decimal import Decimal

from django_email.dto import (
    DeliveryBlock,
    LabelAmount,
    OrderCanceledData,
    OrderConfirmationData,
    OrderItemRow,
    OrderPart,
    PaymentBlock,
    PickupPoint,
    TotalsBlock,
)

from django_checkout.models import Order, PaymentMethod
from django_checkout.schemas.common import format_money
from django_checkout.services.order_mail import PAID_STATUSES, PaymentKind, payment_kind, settling_method
from django_checkout.services.order_mail_catalog import variant_lines
from django_checkout.services.order_mail_group import purchase_orders
from django_checkout.services.order_service import _amount
from django_checkout.utils import payment_entries

ZERO = Decimal("0.00")
MAX_DIGITS = 18  # integer digits an amount may have; Decimal's 28-digit context must still hold it to the cent
POINT_STREET_KEYS = ("address", "street", "address_line1", "line1")
POINT_POSTCODE_KEYS = ("postcode", "post_code", "postal_code", "zip_code", "zip")


def build_confirmation_data(order: Order, language: str) -> OrderConfirmationData:
    """One part per order of the purchase; delivery, payment and addresses from the lead, totals summed."""
    orders = purchase_orders(order)
    lead, bodies = orders[0], [_dict(part.order_body) for part in orders]
    variants = variant_lines(_skus(bodies), _channel_idx(lead), language)
    totals = _totals(bodies)
    body = bodies[0]
    return OrderConfirmationData(
        placed_at=lead.created,
        currency=str(body.get("currency_code") or ""),
        customer_name=_customer_name(body),
        parts=[
            OrderPart(part.pretty_id or "", _items(item, variants)) for part, item in zip(orders, bodies, strict=True)
        ],
        delivery=_delivery(body),
        payment=_payment(lead, bodies, totals, language),
        totals=totals,
        discounts=_discounts(body),
        billing_lines=_address_lines(_address(body, "billing_address"), with_tax_id=True),
        comment=str(body.get("comment") or "") or None,
    )


def build_canceled_data(order: Order, was_paid: bool, language: str) -> OrderCanceledData:
    """The canceled order's own number, items and total."""
    body = _dict(order.order_body)
    variants = variant_lines(_skus([body]), _channel_idx(order), language)
    return OrderCanceledData(
        order_number=order.pretty_id or "",
        placed_at=order.created,
        currency=str(body.get("currency_code") or ""),
        customer_name=_customer_name(body),
        items=_items(body, variants),
        total=_money(body.get("total")) or format_money(ZERO),
        was_paid=was_paid,
    )


# --- blocks ---


def _items(body: dict, variants: dict[str, str]) -> list[OrderItemRow]:
    return [_item(row, variants) for row in _items_raw(body)]


def _item(row: dict, variants: dict[str, str]) -> OrderItemRow:
    sku = str(row.get("sku") or "")
    return OrderItemRow(
        name=str(row.get("name") or sku),
        variant_line=variants.get(sku),
        sku=sku,
        quantity=str(row.get("quantity") or ""),
        unit_price=_money(row.get("unit_price")) or "",
        total_price=_money(row.get("total_price")) or "",
        is_gift=bool(row.get("is_gratis")),
        sub_items=[f"{sub.get('sku', '')} × {sub.get('quantity', '')}" for sub in _dicts(row.get("sub_items"))],
    )


def _delivery(body: dict) -> DeliveryBlock:
    shipping = _dict(body.get("shipping_method"))
    return DeliveryBlock(
        method_name=str(shipping.get("name") or shipping.get("code") or ""),
        price=_money(shipping.get("total_price")) or format_money(ZERO),
        pickup_point=_pickup_point(shipping.get("delivery_point")),
        address_lines=_address_lines(_address(body, "shipping_address")),
    )


def _pickup_point(point: object) -> PickupPoint | None:
    """Keys vary by carrier: the known ones are mapped, the rest ignored."""
    if not isinstance(point, dict) or not point:
        return None
    street = _first(point, POINT_STREET_KEYS)
    city_line = " ".join(part for part in (_first(point, POINT_POSTCODE_KEYS), _first(point, ("city",))) if part)
    name = str(point.get("name") or point.get("code") or "")
    return PickupPoint(name=name, address_lines=[line for line in (street, city_line) if line])


def _payment(lead: Order, bodies: list[dict], totals: TotalsBlock, language: str) -> PaymentBlock:
    method, fee = settling_method(lead), _fee(bodies)
    kind = payment_kind(method)
    offline = kind in (PaymentKind.TRANSFER, PaymentKind.COD)
    return PaymentBlock(
        method_name=_method_name(method, _dict(lead.order_body), language),
        kind=kind,
        is_paid=lead.order_status in PAID_STATUSES and kind == PaymentKind.ONLINE,
        instructions_html=_instructions(method, language) if kind == PaymentKind.TRANSFER else None,
        amount_due=totals.total if offline else None,
        fee=fee,
    )


def _method_name(method: PaymentMethod | None, body: dict, language: str) -> str:
    """The method's name in the e-mail language, else the name the body stored at order time."""
    if name := _dict(getattr(method, "name_t9n", None)).get(language):
        return str(name)
    code = getattr(method, "code", None)
    entry = next((entry for entry in payment_entries(body) if entry.get("code") == code), {})
    return str(entry.get("name") or code or "")


def _instructions(method: PaymentMethod | None, language: str) -> str | None:
    return str(_dict(getattr(method, "description_t9n", None)).get(language) or "") or None


def _fee(bodies: list[dict]) -> str | None:
    """The payment fee plus the cash on delivery fee the shipping method adds, over every part."""
    cod_fees = (_dict(body.get("shipping_method")).get("cash_on_delivery_fee") for body in bodies)
    fee = (_parse(_sum(body.get("fee_price") for body in bodies)) or ZERO) + (_parse(_sum(cod_fees)) or ZERO)
    return format_money(fee) if fee > 0 else None


def _totals(bodies: list[dict]) -> TotalsBlock:
    """Summed over the parts with Decimal; shipping and the fee are already divided between them."""
    carts = [_dict(body.get("cart")) for body in bodies]
    shipping = [_dict(body.get("shipping_method")) for body in bodies]
    return TotalsBlock(
        items=_sum(cart.get("total_price") for cart in carts) or format_money(ZERO),
        delivery=_sum(item.get("total_price") for item in shipping) or format_money(ZERO),
        total=_sum(body.get("total") for body in bodies) or format_money(ZERO),
        payment_fee=_positive(_sum(body.get("fee_price") for body in bodies)),
        discount=_positive(_sum(cart.get("discount_amount") for cart in carts)),
        voucher=_positive(_sum(cart.get("voucher_amount_applied") for cart in carts)),
        tax=_sum(body.get("total_tax") for body in bodies),
    )


def _discounts(body: dict) -> list[LabelAmount]:
    rows = _dicts(_dict(body.get("cart")).get("discounts"))
    found = ((str(row.get("code") or ""), _parse(row.get("price_discount"))) for row in rows)
    return [LabelAmount(label=code, amount=format_money(amount)) for code, amount in found if code and amount]


def _customer_name(body: dict) -> str:
    for key in ("billing_address", "shipping_address"):
        address = _address(body, key)
        name = " ".join(str(address.get(part) or "") for part in ("firstname", "lastname")).strip()
        if name:
            return name
    return ""


def _address_lines(address: dict, with_tax_id: bool = False) -> list[str]:
    name = " ".join(str(address.get(part) or "") for part in ("firstname", "lastname")).strip()
    city_line = " ".join(str(address.get(part) or "") for part in ("postcode", "city")).strip()
    lines = [name, address.get("company"), address.get("street"), city_line, address.get("country_code")]
    if with_tax_id:
        lines.append(address.get("tax_id"))
    return [str(line) for line in lines if line]


# --- defensive readers ---


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _address(body: dict, key: str) -> dict:
    return _dict(_dict(body.get("addresses")).get(key))


def _first(source: dict, keys: tuple[str, ...]) -> str:
    return next((str(source[key]) for key in keys if source.get(key)), "")


def _skus(bodies: list[dict]) -> list[str]:
    return sorted({str(row["sku"]) for body in bodies for row in _items_raw(body) if row.get("sku")})


def _items_raw(body: dict) -> list[dict]:
    return _dicts(_dict(body.get("cart")).get("items"))


def _channel_idx(order: Order) -> str:
    return order.channel.idx if order.channel_id else ""


def _parse(value: object) -> Decimal | None:
    """An amount of the body; unparseable, NaN, infinite or absurdly large (no 2-place quantize) gives None."""
    amount = _amount(value)
    return amount if amount is not None and amount.is_finite() and amount.adjusted() < MAX_DIGITS else None


def _money(value: object) -> str | None:
    amount = _parse(value)
    return None if amount is None else format_money(amount)


def _sum(values) -> str | None:  # noqa: ANN001 - any iterable of raw body values
    amounts = [amount for amount in (_parse(value) for value in values) if amount is not None]
    return format_money(sum(amounts, ZERO)) if amounts else None


def _positive(value: str | None) -> str | None:
    return value if value is not None and Decimal(value) > 0 else None
