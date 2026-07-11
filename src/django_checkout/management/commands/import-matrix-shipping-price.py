# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from argparse import BooleanOptionalAction

from django.core.management.base import BaseCommand

from django_checkout.worker.import_matrix_shipping_price import ShippingPriceMatrixImporter


class Command(BaseCommand):
    help = "Import matrix shipping price from CSV (need file_path, channel_idx)"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="define csv file path")
        parser.add_argument("channel_idx", type=str, help="define csv channel_idx")
        parser.add_argument(
            "--delete_previous", type=bool, help="delete previous data", action=BooleanOptionalAction, default=False
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]
        channel_idx = options["channel_idx"]
        delete_previous = options["delete_previous"]
        ShippingPriceMatrixImporter(
            file_path=file_path, channel_idx=channel_idx, delete_previous=delete_previous
        ).start()
