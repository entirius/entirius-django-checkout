# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Voucher / Gift card payment provider.

Implementation lives in `django_checkout_voucher.payment_provider` (in the
voucher module). This stub re-exports it so the checkout payment_provider
package has a consistent layout with all other providers (autopay, paynow,
payu, stripe, etc.).

If `django-checkout-voucher` is NOT installed, `VoucherPaymentProvider` is
None — the dispatch in `PaymentMethod._get_provider_cls()` falls back to
BasePaymentProvider and raises ImproperlyConfigured at call time.
"""

try:
    from django_checkout_voucher.payment_provider import VoucherPaymentProvider
except ImportError:
    VoucherPaymentProvider = None
