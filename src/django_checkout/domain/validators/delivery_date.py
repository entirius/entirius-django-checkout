# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import datetime

from django_checkout.settings import MAX_REQUESTED_DELIVERY_DATE


def validator_delivery_date(data, checkout_data):
    today = datetime.date.today()
    earliest_allowed_date = today + datetime.timedelta(days=1)
    latest_allowed_date = today + datetime.timedelta(days=MAX_REQUESTED_DELIVERY_DATE)
    # sprawdzenie daty wchodzącej
    if data.requested_delivery_date:
        if earliest_allowed_date <= data.requested_delivery_date <= latest_allowed_date:
            return True, "", data.requested_delivery_date.strftime("%Y-%m-%d")
        else:
            return (
                False,
                f"The provided requested delivery date is not within the range of <tomorrow, {MAX_REQUESTED_DELIVERY_DATE} days>.",
                None,
            )

    # sprawdzenie daty istniejącej w koszyku
    elif checkout_data.requested_delivery_date:
        if earliest_allowed_date <= checkout_data.requested_delivery_date <= latest_allowed_date:
            return True, "", checkout_data.requested_delivery_date.strftime("%Y-%m-%d")
        else:
            return (
                False,
                f"The current cart requested delivery date is not within the range of <tomorrow, {MAX_REQUESTED_DELIVERY_DATE} days>.",
                None,
            )
        return True, "", checkout_data.requested_delivery_date.strftime("%Y-%m-%d")
    else:
        return True, "", None
