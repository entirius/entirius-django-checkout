# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Which of a cart's payment methods does what when a voucher pays next to another method.

``payment_method`` is a list: ``[voucher, payu]`` when a voucher covers part of the cart, in the order the client
sent. Entry ``[0]`` may be the voucher, so it is neither the gateway's own entry nor the method that settles the rest.
Every helper takes PaymentMethod rows (told apart by ``provider``) and payment entries of a body (by ``code``).
"""

from django_checkout.enums import PaymentProvider


def is_voucher_method(method) -> bool:
    return PaymentProvider.is_voucher(getattr(method, "provider", None), getattr(method, "code", None))


def settling_payment_method(methods):
    """The method that settles what the vouchers leave: the first that is no voucher, else the first."""
    present = [method for method in methods or [] if method is not None]
    return next((method for method in present if not is_voucher_method(method)), present[0] if present else None)


def is_voucher_intent(intent) -> bool:
    """A PaymentIntent is told apart by its method's provider; by its code once the method is gone (SET_NULL)."""
    return PaymentProvider.is_voucher(getattr(intent.method, "provider", None), intent.code)


def settling_payment_intent(intents):
    """The intent of the method that settles what the vouchers leave: the first that is no voucher, else the first.

    ``Order.create`` makes no gateway intent when the vouchers cover the order, so the voucher's is the one left.
    """
    present = list(intents)
    return next((intent for intent in present if not is_voucher_intent(intent)), present[0] if present else None)


def covered_by_vouchers(voucher_paid, amount_due) -> bool:
    """True when the vouchers leave nothing to pay: the other methods settle nothing and carry no fee."""
    return voucher_paid > 0 and amount_due <= 0


def vouchers_first(methods) -> list:
    """Vouchers ahead of the other methods, each group in its own order.

    A voucher is debited inside the order's transaction, so a debit that fails rolls the order back. A gateway
    session created before it would survive the rollback and stay payable for an order that does not exist.
    """
    present = [method for method in methods or [] if method is not None]
    return sorted(present, key=lambda method: not is_voucher_method(method))
