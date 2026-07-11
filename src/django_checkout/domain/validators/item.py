# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.enums import ItemStatus, ValidationStatus


def get_item_list_status(item_list) -> ValidationStatus:
    invalid_states = [ItemStatus.INVALID, ItemStatus.OUT_OF_STOCK]
    changed_states = [ItemStatus.CHANGED_QTY, ItemStatus.CHANGED_PRICE, ItemStatus.CHANGED_PRICE_AND_QTY]
    is_invalid = (True if elem.status in invalid_states else False for elem in item_list)
    is_changed = (True if elem.status in changed_states else False for elem in item_list)

    if any(is_invalid):
        return ValidationStatus.INVALID
    elif any(is_changed):
        return ValidationStatus.CHANGED
    else:
        return ValidationStatus.VALID
