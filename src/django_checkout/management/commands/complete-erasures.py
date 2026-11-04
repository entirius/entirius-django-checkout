# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import re

from django.core.management.base import BaseCommand
from django.db.models import Q, QuerySet
from process_logger import ProcessLogger

from django_checkout.models import Cart, Order
from django_checkout.utils import ANONYMIZED_EMAIL_DOMAIN as DEFAULT_MARKER_DOMAIN
from django_checkout.utils.gateway_payload import scrub_gateway_payload

logger_process = ProcessLogger("CHECKOUT_ERASURE")


def _marker(domains: list[str]) -> str:
    return rf"^x+@({'|'.join(re.escape(domain) for domain in domains)})$"


def _erased(body_field: str, marker: str) -> Q:
    """Rows whose body carries an anonymised address e-mail: an earlier erasure went through them."""
    return Q(**{f"{body_field}__addresses__billing_address__email__regex": marker}) | Q(
        **{f"{body_field}__addresses__shipping_address__email__regex": marker}
    )


def _keeps_an_email(order: Order, marker: re.Pattern) -> bool:
    emails = [order.billing_email, order.shipping_email]
    emails += [email for coupon in order.used_coupons.all() for email in (coupon.billing_email, coupon.shipping_email)]
    return any(email and not marker.match(email) for email in emails)


def _keeps_gateway_data(order: Order) -> bool:
    payloads = [payload for i in order.payment_items.all() for payload in (i.provider_request, i.provider_notify)]
    return any(scrub_gateway_payload(payload) != payload for payload in payloads)


class Command(BaseCommand):
    help = (
        "Finish the anonymisation of orders and carts erased before checkout anonymised every personal field "
        "(e-mail columns, used codes, address contact fields, comment, IP, gateway payloads). Dry run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Anonymise them. Without it nothing is written.")
        parser.add_argument(
            "--marker-domain",
            action="append",
            dest="marker_domains",
            help=f"One more domain of an anonymised e-mail besides {DEFAULT_MARKER_DOMAIN} (repeatable): the one an "
            "earlier release line anonymised to, when the database came from it.",
        )

    def handle(self, *args, **options):
        marker = _marker([DEFAULT_MARKER_DOMAIN, *(options["marker_domains"] or [])])
        orders = Order.objects.filter(_erased("order_body", marker)).prefetch_related("used_coupons", "payment_items")
        carts = Cart.objects.filter(_erased("cart_body", marker))
        counts = f"orders {orders.count()}, carts {carts.count()}"
        if not options["apply"]:
            self._report(orders, counts, re.compile(marker))
            return
        failed = self._anonymize(orders) + self._anonymize(carts)
        self.stdout.write(f"Anonymised: {counts}, failed {failed}.")
        logger_process.info("complete_erasures: done", extra={"details": {"counts": counts, "failed": failed}})

    def _report(self, orders: QuerySet, counts: str, marker: re.Pattern) -> None:
        with_email = with_gateway_data = 0
        for order in orders.iterator(chunk_size=500):
            with_email += _keeps_an_email(order, marker)
            with_gateway_data += _keeps_gateway_data(order)
        self.stdout.write(
            f"[dry-run] Erased before: {counts}. Of the orders, {with_email} still hold an e-mail in the columns or "
            f"used codes and {with_gateway_data} gateway payload data. Run with --apply to anonymise them "
            "(addresses, comment and IP included)."
        )

    def _anonymize(self, rows: QuerySet) -> int:
        failed = 0
        for row in rows.iterator(chunk_size=500):
            try:
                row.anonymize()
            except Exception as e:
                logger_process.exception(e)
                failed += 1
        return failed
