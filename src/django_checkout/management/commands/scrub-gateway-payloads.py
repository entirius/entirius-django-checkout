# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from process_logger import ProcessLogger

from django_checkout.models import PaymentIntent
from django_checkout.models.payment_intent import PAYLOAD_FIELDS

logger_process = ProcessLogger("CHECKOUT_GATEWAY_PAYLOADS")


def _reduce_locked(pks: list[int]) -> int:
    """Re-read the payments under a row lock and write what the allowlist drops; how many changed.

    The scan reads a snapshot: a notification may have saved a payment since, and this must not write over it.
    """
    with transaction.atomic():
        payments = PaymentIntent.objects.select_for_update().filter(pk__in=pks).only("pk", *PAYLOAD_FIELDS)
        changed = [payment for payment in payments if payment.reduce_payloads()]
        # bulk_update skips save(): the payloads on the instances are already reduced.
        PaymentIntent.objects.bulk_update(changed, PAYLOAD_FIELDS)
    return len(changed)


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
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1")
        payments = (
            PaymentIntent.objects.exclude(provider_request__isnull=True, provider_notify__isnull=True)
            .only("pk", *PAYLOAD_FIELDS)
            .order_by("pk")
        )
        scanned, found, written, batch = 0, 0, 0, []
        for payment in payments.iterator(chunk_size=batch_size):
            scanned += 1
            if not payment.reduce_payloads():
                continue
            found += 1
            batch.append(payment.pk)
            if len(batch) == batch_size:
                written += self._flush(batch, options["apply"])
                batch = []
        written += self._flush(batch, options["apply"])
        self._report(scanned, written if options["apply"] else found, options["apply"])

    @staticmethod
    def _flush(pks: list[int], apply: bool) -> int:
        return _reduce_locked(pks) if apply and pks else 0

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
