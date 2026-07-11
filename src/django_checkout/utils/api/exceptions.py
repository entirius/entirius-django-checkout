# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_utils.api.exceptions import Forbidden, NotFound


class CartIDNotFound(NotFound):
    message = "Cart ID not found"


class CartOwnedByOtherUser(Forbidden):
    message = "cart_owned_by_other_user"


class UserNotAuthorizedToCart(CartOwnedByOtherUser):
    pass


class GuestNotAuthorizedToCart(CartOwnedByOtherUser):
    pass
