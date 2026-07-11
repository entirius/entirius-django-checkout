# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand
from django.db.models import Q

from django_checkout.models import DiscountRuleCode
from django_checkout.worker.export_discount_rules import export_discount_rules_to_csv


class Command(BaseCommand):
    help = "Export discount rules to csv"

    def add_arguments(self, parser):
        parser.add_argument("--channel_idx", type=str, help="Idx of channel in django_checkout")
        parser.add_argument(
            "--name",
            type=str,
            help="Import discount rules by specified name. You can seperate by ',' Example: socks10,all15",
        )

    def handle(self, *args, **options):
        channel_idx = options["channel_idx"]
        name = options["name"]
        filters = []
        if channel_idx:
            channel_filter = Q(channel__idx=channel_idx)
            filters.append(channel_filter)
        if name:
            names = name.split(",")
            names_filter = Q(name__in=names)
            filters.append(names_filter)

        if filters:
            discount_rules_to_export = DiscountRuleCode.objects.filter(*filters)
        else:
            discount_rules_to_export = DiscountRuleCode.objects.all()

        if not discount_rules_to_export.exists():
            raise Exception("Nothing to import. Code got 0 discount_rules")

        try:
            export_discount_rules_to_csv(discount_rules_to_export)
        except Exception as ex:
            self.stdout.write(self.style.ERROR(ex))
