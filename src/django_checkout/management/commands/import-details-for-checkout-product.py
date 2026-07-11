# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand

from django_checkout.worker.import_details_for_checkout_product import ImportDetailsForCheckoutProduct


class Command(BaseCommand):
    help = "Import details for checkout product from CSV"

    def add_arguments(self, parser):
        parser.add_argument("channel", type=str, help="define csv file path")
        parser.add_argument("file_path", type=str, help="define csv file path")

    def handle(self, *args, **options):
        channel = options["channel"]
        file_path = options["file_path"]

        importer = ImportDetailsForCheckoutProduct(channel, file_path, self)
        importer.start()
