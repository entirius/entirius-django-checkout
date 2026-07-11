# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING, Optional

from django.db import models

from django_checkout.enums import CartStatus
from django_checkout.models.order import Order
from django_checkout.utils.api.exceptions import (
    CartIDNotFound,
    CartOwnedByOtherUser,
    GuestNotAuthorizedToCart,
    UserNotAuthorizedToCart,
)

if TYPE_CHECKING:
    from django_checkout.models.cart import Cart


class CartManager(models.Manager):
    def get_record_or_none(self, request, cart_id) -> Optional["Cart"]:
        """
        Get cart record without raising exceptions.
        Returns None if cart doesn't exist or user is not authorized.
        """
        try:
            return self.get_record(request, cart_id)
        except (CartIDNotFound, CartOwnedByOtherUser, UserNotAuthorizedToCart, GuestNotAuthorizedToCart):
            return None

    def get_record(self, request, cart_id) -> Optional["Cart"]:
        """Resolve a cart for the current request.

        Raises:
            CartIDNotFound: cart with the given UUID does not exist in this channel
                (or is not in status NEW).
            CartOwnedByOtherUser: the cart belongs to a different customer than the
                requesting user, or the requester is a guest and the cart already
                has an owner. Both cases collapse to a single 403 with a uniform
                error code — the frontend remedy (POST /carts/ for a fresh
                cart_id) is the same in both situations.
        """
        customer = request.user.customer if request.user is not None and request.user.is_authenticated else None

        record_only_cart_id = self.filter(cart_id=cart_id, channel=request.channel, cart_status=CartStatus.NEW).first()

        if not record_only_cart_id:
            raise CartIDNotFound

        if customer:
            if record_only_cart_id.customer is None:
                record_only_cart_id.add_customer(customer)
                record_only_cart_id.save(update_fields=["customer"])
                return record_only_cart_id
            if customer != record_only_cart_id.customer:
                raise CartOwnedByOtherUser
            return record_only_cart_id

        if record_only_cart_id.customer is not None:
            raise CartOwnedByOtherUser
        return record_only_cart_id

    def get_latest_by_customer(self, request) -> Optional["Cart"]:
        customer = request.user.customer if request.user is not None and request.user.is_authenticated else None
        if customer is None:
            return None
        else:
            record = self.filter(channel=request.channel, customer=customer, cart_status=CartStatus.NEW).exclude(
                order__in=Order.objects.all()
            )
            if record.exists():
                return record.latest("created")
            else:
                return None

    def get_quest_record(self, request, cart_id) -> Optional["Cart"]:
        record = self.filter(cart_id=cart_id, channel=request.channel, customer=None).first()
        return record
