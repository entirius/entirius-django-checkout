# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from functools import wraps

from django_utils.api.decorators import api_view
from django_utils.api.exceptions import BadRequest, NotFound, Unauthorized

from django_checkout.models import APIAdminKey, APIKey, Channel


def channel_view(view):
    def is_allowed(request):
        key = request.headers.get("X-API-KEY")
        channel = request.channel

        query = APIKey.objects.filter(key=key, channel=channel)

        return query.exists()

    @wraps(view)
    @api_view
    def _wrapped(request, channel_idx=None, *args, **kwargs):
        channel = Channel.objects.filter(idx=channel_idx).first()
        request.channel = channel

        if channel is not None:
            passed = is_allowed(request)
            if passed:
                response = view(request, channel_idx=channel_idx, *args, **kwargs)
                return response
            else:
                raise BadRequest("Invalid api key")
        else:
            raise NotFound("Channel does not exist")

    return _wrapped


def admin_view(view):
    def is_allowed(request):
        key = request.headers.get("X-API-ADMIN-KEY")
        channel = request.channel

        query = APIAdminKey.objects.filter(key=key, channel=channel)

        return query.exists()

    @wraps(view)
    @api_view
    def _wrapped(request, channel_idx=None, *args, **kwargs):
        channel = Channel.objects.filter(idx=channel_idx).first()
        request.channel = channel

        if channel is not None:
            passed = is_allowed(request)
            if passed:
                response = view(request, channel_idx=channel_idx, *args, **kwargs)
                return response
            else:
                raise Unauthorized("Invalid api admin key")
        else:
            raise NotFound("Channel does not exist")

    return _wrapped
