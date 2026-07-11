# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.management.base import BaseCommand

from django_checkout.worker.import_discount_rules import import_discount_rules_from_csv


class Command(BaseCommand):
    help = "Import Discount Rules from CSV"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **options):
        file_path = options["file_path"]

        errors = 0
        number_discount_rules_csv = 0
        try:
            errors, number_discount_rules_csv = import_discount_rules_from_csv(filepath=file_path)
        except Exception as e:
            print(f"{e}")

        print(
            f"Zostało utworzonych {number_discount_rules_csv - errors} na {number_discount_rules_csv} z pliku"
            f" {file_path}"
        )
