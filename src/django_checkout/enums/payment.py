# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import re
from enum import unique

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _

from django_checkout import settings


@unique
class PaymentProvider(TextChoices):
    TRANSFER = "transfer", _("Bank transfer")
    COD = "cod", _("Cash on delivery")
    FREE_ORDER = "free_order", _("Free order")
    PAYU = "payu", _("PayU")
    PAYU_CARD = "payu_card", _("PayU Card")
    PAYU_BLIK = "payu_blik", _("PayU BLIK")
    PAYPAL = "paypal", _("PayPal")
    PAYNOW = "paynow", _("PayNow")
    PRZELEWY24 = "przelewy24", _("Przelewy24")
    CASH = "cash", _("Cash")
    DEFERRED_PAYMENT = "deferred_payment", _("Deferred payment")
    AUTOPAY = "autopay", _("Autopay")
    STRIPE = "stripe", _("Stripe")
    VOUCHER = "voucher", _("Voucher / Gift card")

    @staticmethod
    def requires_code():
        """
        Returns a list of payment providers that require a pay code.
        """
        return [PaymentProvider.PAYU_BLIK, PaymentProvider.VOUCHER]

    @staticmethod
    def with_cart_attachments():
        """Providers whose attach_to_cart persists rows on the cart (e.g. voucher
        -> CartVoucher). process_cart clears these before re-applying when the
        client sends a declarative payment_method section."""
        return [PaymentProvider.VOUCHER]

    @staticmethod
    def is_voucher(provider=None, code: str | None = None) -> bool:  # noqa: ANN001
        """Voucher discriminator — single source of truth. Trust ``provider``, fall
        back to ``code`` (the PaymentMethod FK is SET_NULL and admins may name the
        method code differently). Do not re-implement ``provider == "voucher"``."""
        if provider is not None:
            return str(provider) == PaymentProvider.VOUCHER
        return code == PaymentProvider.VOUCHER

    @staticmethod
    def validate_code(code: str, provider) -> bool:
        """
        Validates if the provided code matches the payment provider's code.
        """
        if provider == PaymentProvider.PAYU_BLIK:
            return bool(re.fullmatch(rf"{settings.BLIK_REGEX_VALIDATION}", code))
        if provider == PaymentProvider.VOUCHER:
            # Voucher uses pay_code as "code1:pin1,code2:pin2" (PIN optional).
            # Format validation is loose; semantic validation happens in
            # VoucherPaymentProvider against the Voucher table.
            return bool(code) and bool(re.fullmatch(r"[A-Za-z0-9_\-:,]+", code))
        return True
