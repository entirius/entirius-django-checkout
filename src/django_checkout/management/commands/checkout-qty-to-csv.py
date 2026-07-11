# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand

from django_checkout.models import Stock


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("channel_idx", type=str)
        parser.add_argument("--supplier_code", type=str)

    def handle(self, *args, **options):
        channel_idx = options["channel_idx"]
        supplier_code = options["supplier_code"]
        file_path = os.path.join(settings.EXPORT_DIR, f"checkout-qty-{datetime.now()}")
        with open(file_path, "w") as output_file:
            query = {"product__channel__idx": channel_idx}
            if supplier_code:
                query["supplier__code"] = supplier_code
            else:
                query["supplier__channel__idx"] = channel_idx
                query["supplier__is_global"] = True
            stocks = Stock.objects.filter(**query)
            for stock in stocks:
                output_file.write(f"{stock.product.sku},{stock.quantity}")
                output_file.write("\n")
        print(f"Quantities exported to file: {file_path}")
        self.stdout.write(self.style.SUCCESS("done"))
