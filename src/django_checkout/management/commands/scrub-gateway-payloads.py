# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand
from process_logger import ProcessLogger

from django_checkout.models import PaymentIntent
from django_checkout.models.payment_intent import PAYLOAD_FIELDS
from django_checkout.utils.gateway_payload import scrub_gateway_payload

logger_process = ProcessLogger("CHECKOUT_GATEWAY_PAYLOADS")


def _reduce(payment: PaymentIntent) -> bool:
    """Put the payment's payloads through the allowlist on the instance; True if that changed one of them."""
    changed = False
    for name in PAYLOAD_FIELDS:
        stored = getattr(payment, name)
        if (scrubbed := scrub_gateway_payload(stored)) != stored:
            setattr(payment, name, scrubbed)
            changed = True
    return changed


class Command(BaseCommand):
    help = (
        "Reduce the gateway payloads of payments stored before every save went through the allowlist "
        "(provider_request, provider_notify: ids, amounts, currencies, statuses, timestamps). Dry run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the reduced payloads. Without it nothing is.")
        parser.add_argument("--batch-size", type=int, default=500, help="Rows read and written per batch (500).")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        payments = (
            PaymentIntent.objects.exclude(provider_request__isnull=True, provider_notify__isnull=True)
            .only("pk", *PAYLOAD_FIELDS)
            .order_by("pk")
        )
        scanned, reduced, batch = 0, 0, []
        for payment in payments.iterator(chunk_size=batch_size):
            scanned += 1
            if _reduce(payment):
                reduced += 1
                batch.append(payment)
            if len(batch) >= batch_size:
                self._flush(batch, options["apply"])
                batch = []
        self._flush(batch, options["apply"])
        self._report(scanned, reduced, options["apply"])

    @staticmethod
    def _flush(batch: list[PaymentIntent], apply: bool) -> None:
        # bulk_update skips save(): the payloads on the instances are already reduced.
        if apply and batch:
            PaymentIntent.objects.bulk_update(batch, PAYLOAD_FIELDS)

    def _report(self, scanned: int, reduced: int, applied: bool) -> None:
        if applied:
            self.stdout.write(f"Reduced the gateway payloads of {reduced} of {scanned} payments.")
            logger_process.add_log_param_once("scanned", scanned)
            logger_process.add_log_param_once("reduced", reduced)
            logger_process.info("scrub_gateway_payloads: done")
            return
        self.stdout.write(
            f"[dry-run] Payments with gateway payloads: {scanned}; {reduced} hold data the allowlist drops. "
            "Run with --apply to reduce them."
        )
