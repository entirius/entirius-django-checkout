# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework.permissions import BasePermission

from django_checkout.models import APIKey, Channel
from django_checkout.utils.api_keys import STOREFRONT_SCOPE, access_installed, key_is_valid


class ChannelAPIKeyPermission(BasePermission):
    """Validate the X-API-KEY header for the URL's channel.

    Same check as the v1 @channel_view decorator, but as DRF permission class.
    Sets request.checkout_channel on success. With django_access installed the key is an access token
    (``utils.api_keys.key_is_valid``) and an unknown channel is refused like a bad key, before the key is checked,
    so a refused request never counts as a token's use. Without it: the single joined legacy query, unchanged.
    """

    def has_permission(self, request, view) -> bool:
        # SECURITY: channel-scoped keys only — no global fallback
        channel_idx = view.kwargs.get("channel_idx")
        if access_installed():
            return self._token_permission(request, channel_idx)
        return self._legacy_permission(request, channel_idx)

    @staticmethod
    def _token_permission(request, channel_idx: str | None) -> bool:
        channel = Channel.objects.filter(idx=channel_idx).first()
        if channel is None or not key_is_valid(request, scope=STOREFRONT_SCOPE, channel_idx=channel_idx):
            return False
        request.checkout_channel = channel
        return True

    @staticmethod
    def _legacy_permission(request, channel_idx: str | None) -> bool:
        api_key = request.META.get("HTTP_X_API_KEY")
        if not api_key:
            return False
        try:
            key_obj = APIKey.objects.select_related("channel").get(key=api_key, channel__idx=channel_idx)
        except (APIKey.DoesNotExist, APIKey.MultipleObjectsReturned):
            return False
        request.checkout_channel = key_obj.channel
        return True
