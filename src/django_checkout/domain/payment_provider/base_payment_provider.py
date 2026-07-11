# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.core.handlers.wsgi import WSGIRequest

from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import PaymentIntent, PaymentMethod


class BasePaymentProvider:
    redirect_url: str
    order_id: str
    payment_method: "PaymentMethod"
    request: WSGIRequest
    error: bool
    customer: any

    def __init__(self, payment_method: "PaymentMethod") -> None:
        self.payment_method = payment_method
        self.redirect_url = ""
        self.order_id = ""
        self.provider_request = {}
        self.provider_notify = {}
        self.error = False
        self.customer = None

    def get_payment_method_extension(self, cart=None) -> dict:
        """Fetch additional fields for PaymentMethod needed to complete the transaction"""
        return {}

    def attach_to_cart(self, cart, payment_data=None) -> list[str]:
        """Provider-specific side-effects after this payment method is attached to a cart.

        Default no-op. Subclasses override to apply codes (e.g. voucher pay_code →
        CartVoucher rows). Returns a list of human-readable error messages — empty
        when the attach succeeded fully.

        ``payment_data`` is the RAW payment-method input of the current request
        (list of PaymentData or legacy dicts). Secrets like voucher pay_code are
        redacted from the persisted cart_body, so providers must read codes from
        this parameter — the body fallback only works for non-secret fields.

        Called once per validate_cart_data cycle — keep idempotent (use
        get_or_create / upsert). NOT a place for blocking I/O.
        """
        return []

    def clear_cart_attachments(self, cart) -> None:
        """Remove every row this provider attached to the cart (e.g. CartVoucher).

        Called by process_cart BEFORE validation/attach when the client request
        carries a declarative payment_method section — the section is the FULL
        desired state, so codes absent from it must be detached. Default no-op.
        """
        return None

    def get_order_extension(self, order) -> dict:
        """Generate additional fields for order required to complete payment"""
        return {}

    def get_redirection(self) -> str:
        return self.redirect_url

    def process_order(self, order, cart) -> str:
        return ""

    def get_processed_amount(self, order):
        """Settled amount this provider actually covers on the order, or None.

        Called by Order.create right after process_order(). When a provider can
        derive the true covered amount from its own records (e.g. voucher
        redemptions debited at placement), returning it lets Order.create
        reconcile the PaymentIntent.amount snapshot with reality instead of
        trusting the cart aggregate. Default None = keep the snapshot.
        """
        return None

    def process_payment(self, payment: "PaymentIntent", is_cash_on_delivery=False, is_free_order=False):
        payment.external_order_id = self.order_id
        payment.redirect_url = self.redirect_url
        if is_cash_on_delivery or is_free_order:
            payment.payment_status = PaymentIntentStatus.COMPLETE
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = self.provider_request
        payment.save()
