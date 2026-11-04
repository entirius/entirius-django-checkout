# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q, QuerySet
from process_logger import ProcessLoggerMixin

from django_checkout.enums import OrderStatus
from django_checkout.models import Cart, Channel, Order, UsedCoupon
from django_checkout.signals import customer_anonymized_signal
from django_checkout.utils import anonymize_email_in_body

# The shop has nothing left to do with an order in these statuses: delivered, returned or cancelled. Any other one
# (waiting for payment, confirmed, on hold, in progress) still needs the delivery address and the contact, so an
# erasure is refused while the customer has such an order.
SETTLED_ORDER_STATUSES = (OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED)


@dataclass(frozen=True)
class BlockingOrder:
    order_id: str
    pretty_id: str | None
    status: str


class ErasureBlocked(Exception):
    """The customer has orders the shop has not fulfilled yet; nothing was anonymised."""

    def __init__(self, orders: list[BlockingOrder]):
        super().__init__(f"Erasure refused: {len(orders)} order(s) not fulfilled yet")
        self.orders = orders


@dataclass
class _Erasure:
    success: bool = True
    order_ids: list[str] = field(default_factory=list)
    orders_idxs: list[str] = field(default_factory=list)
    carts_idxs: list[str] = field(default_factory=list)
    orders_fail: list[str] = field(default_factory=list)
    carts_fail: list[str] = field(default_factory=list)

    def result(self) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        lists = (self.orders_idxs, self.carts_idxs, self.orders_fail, self.carts_fail)
        return self.success, *(list(set(ids)) for ids in lists)


def _in_channel(queryset: QuerySet, channel: Channel | None) -> QuerySet:
    return queryset if channel is None else queryset.filter(channel=channel)


def _refuse_unsettled(orders: list[Order]) -> None:
    blocking = [
        BlockingOrder(str(order.order_id), order.pretty_id_snap, order.order_status)
        for order in orders
        if order.order_status not in SETTLED_ORDER_STATUSES
    ]
    if blocking:
        raise ErasureBlocked(blocking)


def _address_email(body_field: str, email: str) -> Q:
    """The billing or the shipping address of the body holds ``email`` (the whole address, case aside)."""
    return Q(**{f"{body_field}__addresses__billing_address__email__iexact": email}) | Q(
        **{f"{body_field}__addresses__shipping_address__email__iexact": email}
    )


def _orders_of(email: str) -> QuerySet:
    """The customer's orders: by the account, the e-mail columns, the address e-mails and the used codes.

    The columns and the used codes find an order whose body an earlier erasure already anonymised, so a repeated
    request completes it; a used code also holds the account e-mail of a customer whose account is gone.
    """
    used_codes = UsedCoupon.objects.filter(Q(billing_email__iexact=email) | Q(shipping_email__iexact=email))
    return Order.objects.filter(
        Q(customer__user__email__iexact=email)
        | Q(billing_email__iexact=email)
        | Q(shipping_email__iexact=email)
        | Q(pk__in=used_codes.values("order_id"))
        | _address_email("order_body", email)
    )


def _carts_of(email: str) -> QuerySet:
    return Cart.objects.filter(Q(customer__user__email__iexact=email) | _address_email("cart_body", email))


def _replace_mention(model: type[Order] | type[Cart], pk: int, body_field: str, email: str) -> str | None:
    """Replace ``email`` in a fresh, locked body of another customer's row; its id, or None when it held none."""
    with transaction.atomic():
        row = model.objects.select_for_update().get(pk=pk)
        if not anonymize_email_in_body(getattr(row, body_field), email):
            return None
        model.objects.filter(pk=pk).update(**{body_field: getattr(row, body_field)})
    return str(row.order_id if model is Order else row.cart_id)


class CustomerService(ProcessLoggerMixin):
    def anonymize_customer(
        self, email: str, channel: Channel | None = None
    ) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        """Anonymise the orders and carts of ``email``: in ``channel`` only, or in every channel when None.

        Another customer's order or cart that holds the e-mail in some other field keeps everything but that e-mail.
        Raises ``ErasureBlocked`` before anything is written while one of the customer's orders is not settled.
        """
        own = _in_channel(_orders_of(email), channel)
        orders = list(own.select_related("cart").prefetch_related("used_coupons", "payment_items"))
        _refuse_unsettled(orders)
        erasure = _Erasure()
        for order in orders:
            if self._anonymize_order(order, erasure) and order.cart is not None:
                self._anonymize_cart(order.cart, erasure)
        others = _in_channel(Order.objects.exclude(pk__in=own.values("pk")), channel)
        self._replace_mentions(others, "order_body", email, erasure)
        self._erase_carts(email, channel, erasure)
        self._notify(email, erasure, channel)
        return erasure.result()

    def _erase_carts(self, email: str, channel: Channel | None, erasure: _Erasure) -> None:
        own = _in_channel(_carts_of(email), channel)
        for cart in own:
            self._anonymize_cart(cart, erasure)
        others = _in_channel(Cart.objects.exclude(pk__in=own.values("pk")), channel)
        self._replace_mentions(others, "cart_body", email, erasure)

    def _replace_mentions(self, rows: QuerySet, body_field: str, email: str, erasure: _Erasure) -> None:
        """Other customers' rows that hold the e-mail elsewhere: only that e-mail changes."""
        is_order = body_field == "order_body"
        done, failed = (
            (erasure.orders_idxs, erasure.orders_fail) if is_order else (erasure.carts_idxs, erasure.carts_fail)
        )
        for pk in rows.filter(**{f"{body_field}__icontains": email}).values_list("pk", flat=True):
            try:
                row_id = _replace_mention(rows.model, pk, body_field, email)
            except Exception as e:
                self.logger.exception(e)
                failed.append(str(pk))
                erasure.success = False
                continue
            if row_id is not None:
                done.append(row_id)

    def _anonymize_order(self, order: Order, erasure: _Erasure) -> bool:
        """False when anonymising the order failed (its cart is then left alone)."""
        erasure.order_ids.append(str(order.order_id))
        self.logger.add_log_param_once("order_id", order.pretty_id)
        self.logger.add_log_param_once("order_idx", str(order.order_id))
        try:
            if order.anonymize():
                erasure.orders_idxs.append(str(order.order_id))
        except Exception as e:
            self.logger.exception(e)
            erasure.orders_fail.append(str(order.order_id))
            erasure.success = False
            return False
        self.logger.delete_few_log_param(["order_id", "order_idx"])
        return True

    def _anonymize_cart(self, cart: Cart, erasure: _Erasure) -> None:
        self.logger.add_log_param_once("cart_id", str(cart.cart_id))
        try:
            if cart.anonymize():
                erasure.carts_idxs.append(str(cart.cart_id))
        except Exception as e:
            self.logger.exception(e)
            erasure.carts_fail.append(str(cart.cart_id))
            erasure.success = False
            return
        self.logger.delete_log_param("cart_id")

    def _notify(self, email: str, erasure: _Erasure, channel: Channel | None) -> None:
        # send_robust so a failing downstream receiver (e.g. voucher PII scrub) can't
        # abort the erasure; a receiver exception still flips success=False.
        for _, response in customer_anonymized_signal.send_robust(
            sender=self.__class__,
            email=email,
            order_ids=list(set(erasure.order_ids)),
            channel_idx=channel.idx if channel is not None else None,
        ):
            if isinstance(response, Exception):
                self.logger.exception(response)
                erasure.success = False
