# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework.permissions import BasePermission

from django_checkout.models import Channel
from django_checkout.utils.api_keys import STOREFRONT_SCOPE, key_is_valid


class ChannelAPIKeyPermission(BasePermission):
    """Validate the X-API-KEY header for the URL's channel (``utils.api_keys.key_is_valid``).

    Same check as the v1 @channel_view decorator, but as DRF permission class.
    Sets request.checkout_channel on success; an unknown channel is refused like a bad key (before the key is
    checked, so a refused request never counts as a token's use).
    """

    def has_permission(self, request, view) -> bool:
        # SECURITY: channel-scoped keys only — no global fallback
        channel_idx = view.kwargs.get("channel_idx")
        channel = Channel.objects.filter(idx=channel_idx).first()
        if channel is None or not key_is_valid(request, scope=STOREFRONT_SCOPE, channel_idx=channel_idx):
            return False

        request.checkout_channel = channel
        return True
