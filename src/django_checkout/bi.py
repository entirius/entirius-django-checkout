# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from bievents import BiEventAbstract
from django.conf import settings

BI_SOURCE = "django-checkout"
BI_ENVIRONMENT = settings.BI_ENVIRONMENT
BI_BUSINESS_UNIT = settings.BI_BUSINESS_UNIT


class EventAbstract(BiEventAbstract):
    details_type = "Event Abstract"
    version = 2

    def __init__(self, **kwargs):
        kwargs["source"] = BI_SOURCE
        kwargs["environment"] = BI_ENVIRONMENT
        kwargs["business_unit"] = BI_BUSINESS_UNIT
        super().__init__(**kwargs)


class Checkout_PayuNotifyEvent(EventAbstract):
    details_type = "Checkout Payu Notify"
    version = 1


class Checkout_Przelewy24NotifyEvent(EventAbstract):
    details_type = "Checkout Przelewy24 Notify"
    version = 1


class Checkout_OrderCreationEvent(EventAbstract):
    details_type = "Checkout Order Creation"
    version = 1


class Checkout_UpdateStocksFromQmsEvent(EventAbstract):
    details_type = "Checkout Update Stocks from QMS"
    version = 1


class Checkout_PayPalReturnEvent(EventAbstract):
    details_type = "Checkout PayPal Return"
    version = 1


class Checkout_PayPalCancelEvent(EventAbstract):
    details_type = "Checkout PayPal Cancel"
    version = 1


class Checkout_AutopayReturnEvent(EventAbstract):
    details_type = "Checkout Autopay Return"
    version = 1


class Checkout_PayPalNotifyEvent(EventAbstract):
    details_type = "Checkout PayPal Cancel"
    version = 1


class Checkout_PayNowNotifyEvent(EventAbstract):
    details_type = "Checkout PayNow Notify"
    version = 1


class Checkout_StripeNotifyEvent(EventAbstract):
    details_type = "Checkout Stripe Notify"
    version = 1
