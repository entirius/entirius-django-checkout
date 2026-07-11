# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework.permissions import BasePermission

from django_checkout.models import APIKey


class ChannelAPIKeyPermission(BasePermission):
    """Validate X-API-KEY header against checkout APIKey model.

    Same logic as v1 @channel_view decorator, but as DRF permission class.
    Sets request.checkout_channel on success.
    """

    def has_permission(self, request, view) -> bool:
        api_key = request.META.get("HTTP_X_API_KEY")
        if not api_key:
            return False

        # SECURITY: channel-scoped keys only — no global fallback
        channel_idx = view.kwargs.get("channel_idx")
        try:
            key_obj = APIKey.objects.select_related("channel").get(key=api_key, channel__idx=channel_idx)
        except (APIKey.DoesNotExist, APIKey.MultipleObjectsReturned):
            return False

        request.checkout_channel = key_obj.channel
        return True
