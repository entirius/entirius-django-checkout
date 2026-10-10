# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Path parameters shared by the storefront v2 views, described once for the OpenAPI spec."""

from drf_spectacular.utils import OpenApiParameter

CHANNEL_PARAM = OpenApiParameter(name="channel_idx", location="path", description="Channel identifier")
CART_PARAM = OpenApiParameter(name="cart_id", location="path", description="Cart UUID")
ORDER_PARAM = OpenApiParameter(name="order_id", location="path", description="Human-readable order ID or order UUID")
PRETTY_ID_PARAM = OpenApiParameter(
    name="pretty_id", location="path", description="Human-readable order ID or order UUID"
)
FILE_PARAM = OpenApiParameter(name="file_id", location="path", description="Attachment ID")
