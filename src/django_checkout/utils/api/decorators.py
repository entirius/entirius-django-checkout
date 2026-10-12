# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from functools import wraps

from django_utils.api.decorators import api_view
from django_utils.api.exceptions import BadRequest, NotFound, Unauthorized

from django_checkout.models import Channel
from django_checkout.utils.api_keys import ERASE_SCOPE, STOREFRONT_SCOPE, key_is_valid


def channel_view(view):
    @wraps(view)
    @api_view
    def _wrapped(request, channel_idx=None, *args, **kwargs):
        channel = Channel.objects.filter(idx=channel_idx).first()
        request.channel = channel

        if channel is not None:
            passed = key_is_valid(request, scope=STOREFRONT_SCOPE, channel_idx=channel_idx)
            if passed:
                response = view(request, channel_idx=channel_idx, *args, **kwargs)
                return response
            else:
                raise BadRequest("Invalid api key")
        else:
            raise NotFound("Channel does not exist")

    return _wrapped


def admin_view(view):
    @wraps(view)
    @api_view
    def _wrapped(request, channel_idx=None, *args, **kwargs):
        channel = Channel.objects.filter(idx=channel_idx).first()
        request.channel = channel

        if channel is not None:
            passed = key_is_valid(request, scope=ERASE_SCOPE, channel_idx=channel_idx)
            if passed:
                response = view(request, channel_idx=channel_idx, *args, **kwargs)
                return response
            else:
                raise Unauthorized("Invalid api admin key")
        else:
            raise NotFound("Channel does not exist")

    return _wrapped
