# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.models.discount_mode_of_actions import DiscountModeOfAction


# Alias for backward compatibility and clearer naming
# This points to the same table as DiscountModeOfAction but with a clearer name
class GratisProductFilter(DiscountModeOfAction):
    """
    Filter that defines which products can be selected as gratis (free products).
    Also used for percent/price discounts to filter which products receive the discount.

    This is the new name for DiscountModeOfAction, which better reflects its purpose.
    The underlying database table will be renamed in migrations.
    """

    class Meta:
        proxy = True
        ordering = ["-id"]
