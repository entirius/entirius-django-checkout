# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db.models import Q
from django.http import Http404

from django_checkout.models import Cart, Channel


class CheckoutChannelMixin:
    """Mixin for v2 views — resolves channel from URL or API key, loads cart records."""

    def get_channel(self) -> Channel:
        """Get channel from request (set by ChannelAPIKeyPermission) or URL kwarg."""
        if hasattr(self.request, "checkout_channel") and self.request.checkout_channel:
            return self.request.checkout_channel
        channel_idx = self.kwargs.get("channel_idx")
        if not channel_idx:
            raise Http404
        try:
            return Channel.objects.get(idx=channel_idx)
        except Channel.DoesNotExist:
            raise Http404

    def get_customer(self):
        """Resolve customer from JWT auth. Returns None for anonymous users."""
        user = self.request.user
        if not user or not user.is_authenticated:
            return None
        if hasattr(user, "customer"):
            return user.customer
        return None

    def get_cart_record(self, cart_id: str) -> Cart:
        """Load cart by cart_id, channel, and customer ownership. Raises 404 if not found.

        Ownership rules:
        - Authenticated user: sees own carts + guest carts (customer=None)
        - Guest (no JWT): sees only guest carts (customer=None)
        - Nobody sees another customer's cart
        """
        channel = self.get_channel()
        customer = self.get_customer()
        qs = Cart.objects.filter(cart_id=cart_id, channel=channel)
        if customer:
            qs = qs.filter(Q(customer=customer) | Q(customer__isnull=True))
        else:
            qs = qs.filter(customer__isnull=True)
        try:
            return qs.get()
        except Cart.DoesNotExist:
            raise Http404
