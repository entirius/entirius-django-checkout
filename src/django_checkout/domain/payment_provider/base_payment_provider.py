# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from datetime import timedelta
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from django.core.handlers.wsgi import WSGIRequest
from django.urls import reverse
from django.utils import timezone

from django_checkout import settings
from django_checkout.domain.payment_redirect_target import is_allowed_bridge_target
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Order, PaymentIntent, PaymentMethod


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

    def resolve_continue_url(self, raw_url: str | None, order: "Order") -> str | None:
        """Return the final URL handed to the payment gateway.

        - Scheme on PAYMENT_REDIRECT_DIRECT_SCHEMES → returns raw_url unchanged.
        - Target allowed by is_allowed_bridge_target (scheme + host for http/https)
          → creates a PaymentRedirect and returns the backend bridge URL (HTTPS)
          that 302-redirects to raw_url when called.
        - Anything else → ValueError (fail fast, before any redirect row exists).

        raw_url comes from the client, so the scheme alone is not enough: without the
        host check the bridge would be an open redirector on the shop's domain and
        certificate.
        """
        # Local import — the model must load at runtime only, to avoid import cycles
        # (apps registry → models/__init__.py → providers → base_payment_provider).
        from django_checkout.models import PaymentRedirect

        if not raw_url:
            return raw_url

        parsed = urlparse(raw_url)
        if parsed.scheme in settings.PAYMENT_REDIRECT_DIRECT_SCHEMES:
            return raw_url
        if not is_allowed_bridge_target(parsed):
            raise ValueError(f"continue_url target '{parsed.scheme}://{parsed.hostname or ''}' is not allowed")

        redirect_obj = PaymentRedirect.objects.create(
            order=order,
            target_url=raw_url,
            expires_at=timezone.now() + timedelta(minutes=settings.PAYMENT_REDIRECT_BRIDGE_TTL_MINUTES),
        )
        return self.request.build_absolute_uri(
            reverse(
                "payment-redirect-bridge",
                kwargs={
                    "version": settings.API_VERSION,
                    "channel_idx": self.payment_method.channel.idx,
                    "token": str(redirect_obj.token),
                },
            )
        )

    def process_payment(self, payment: "PaymentIntent", is_cash_on_delivery=False, is_free_order=False):
        payment.external_order_id = self.order_id
        payment.redirect_url = self.redirect_url
        if is_cash_on_delivery or is_free_order:
            payment.payment_status = PaymentIntentStatus.COMPLETE
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = self.provider_request
        payment.save()
