# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand

from django_checkout.models.linked_products import LinkedProducts
from django_checkout.worker.import_limit_type import import_product_link_or_limitation_from_csv


class Command(BaseCommand):
    help = "Import relation between shipping method and product from CSV (need file_path, shipping_method)"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="define csv file path")

    def handle(self, *args, **options):
        file_path = options["file_path"]

        errors = 0
        number_of_links = 0
        try:
            errors, number_of_links = import_product_link_or_limitation_from_csv(LinkedProducts, file_path=file_path)
        except Exception as e:
            print(f"{e}")

        print(f"Zostało utworzonych {number_of_links - errors} na {number_of_links} z pliku {file_path}")
