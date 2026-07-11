# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from process_logger import ProcessLogger

from django_checkout.enums import CartStatus
from django_checkout.models import Cart

logger_process = ProcessLogger("CHECKOUT_CART")


class Command(BaseCommand):
    help = "Delete stranded NEW carts older than --days days that never produced an order."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Age threshold in days (compared against Cart.updated). Default: 30.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be deleted without touching the database.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        stranded = Cart.objects.filter(cart_status=CartStatus.NEW, updated__lt=cutoff, order__isnull=True)
        count = stranded.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"[dry-run] would delete {count} stranded carts (older than {days}d).")
            )
            return

        if count == 0:
            self.stdout.write("No stranded carts to delete.")
            logger_process.info("cleanup_stranded_carts: nothing to delete", extra={"details": {"days": days}})
            return

        deleted, _ = stranded.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} stranded carts (older than {days}d)."))
        logger_process.info(
            "cleanup_stranded_carts: deleted stranded carts",
            extra={"details": {"days": days, "deleted": deleted}},
        )
