# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Discount code usage of an order, shared by the v1 and the v2 order views.

An order counts each code it took on ``DiscountCode.current_used`` (what the cart checks ``max_used`` against) and
writes a ``UsedCoupon`` row per code (what ``max_uses_per_user`` is counted from). The caller runs it in the order's
transaction, so an order and its code uses commit together.
"""

from django.db.models import F, Q

from django_checkout.models import DiscountCode, UsedCoupon


class DiscountCodeRefused(Exception):
    """An order cannot take a code its cart applied; ``reason`` is the cart's rejection reason for it."""

    def __init__(self, code: str, reason: str | None):
        super().__init__(f"Discount code {code} can no longer be used ({reason})")
        self.code = code
        self.reason = reason


def applied_code(discount: dict, channel, currency: str | None) -> DiscountCode | None:
    """The code row the cart applied ``discount`` from.

    A code is not unique: one code may sit on an EUR and a PLN rule, or in another channel. The cart applied it from
    a rule of this channel, of its currency (or of none), of the same kind (typed or automatic) and modifier; of rows
    that still tie, the lowest pk, as ``validate_discounts`` picks.
    """
    rows = DiscountCode.objects.filter(
        code=discount["code"],
        rule__channels=channel,
        rule__automatic_applications=bool(discount.get("is_automatic")),
    )
    if currency:
        rows = rows.filter(Q(rule__currencies__isnull=True) | Q(rule__currencies__iso3=currency.upper()))
    if discount.get("modifier"):
        rows = rows.filter(rule__modifier=discount["modifier"])
    return rows.order_by("pk").first()


def use_discounts(discounts: list, channel, order=None) -> None:
    """Count each code of ``discounts`` (``order_body["cart"]["discounts"]``) and record who used it.

    Raises ``DiscountCodeRefused`` when a typed code has no use left; the caller's transaction then rolls the order
    back. Without ``order`` only the counters move.
    """
    currency = order.order_body.get("currency_code") if order else None
    for discount in discounts:
        code = discount.get("code")
        if not code:
            continue
        _take_use(discount, applied_code(discount, channel, currency))
        if order:
            _record_user(order, channel, code)


def _take_use(discount: dict, code_row: DiscountCode | None) -> None:
    """One use of ``code_row``, only while it has one left: two orders racing for the last use cannot both take it.

    The update waits for a concurrent order's lock on the row and re-reads it once that order commits.
    """
    # None: an automatic rule without codes stands in the cart under its name and has no usage count
    if code_row is None:
        return
    taken = DiscountCode.objects.filter(pk=code_row.pk, current_used__lt=F("max_used")).update(
        current_used=F("current_used") + 1
    )
    # The cart applies automatic rules whatever their codes' counters say, so only a typed code is refused.
    if not taken and not discount.get("is_automatic"):
        raise DiscountCodeRefused(code_row.code, "usage_limit_reached")


def _record_user(order, channel, code: str) -> None:
    billing_email, shipping_email = _order_emails(order)
    if billing_email or shipping_email:
        UsedCoupon.objects.create(
            channel=channel,
            order=order,
            customer=order.customer,
            billing_email=billing_email or "",
            shipping_email=shipping_email or "",
            discount_code=code,
        )


def _order_emails(order) -> tuple[str | None, str | None]:
    """The order's e-mail addresses, else the customer's account e-mail and the addresses in the order body."""
    addresses = order.order_body.get("addresses") or {}
    billing_email = order.billing_email
    if not billing_email:
        if order.customer and hasattr(order.customer, "user"):
            billing_email = order.customer.user.email
        else:
            billing_email = (addresses.get("billing_address") or {}).get("email")
    shipping_email = order.shipping_email or (addresses.get("shipping_address") or {}).get("email")
    return billing_email, shipping_email
