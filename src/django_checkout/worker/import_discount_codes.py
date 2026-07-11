# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import csv
from io import StringIO

from django.db import IntegrityError, transaction

from django_checkout.models import DiscountCode


class DiscountCodeImportService:
    def __init__(self, logger):
        self.logger = logger

    def import_discount_codes(self, csv_file, discount_rule, overwrite_existing, delete_old):
        csv_data = csv_file.read().decode("utf-8")
        csv_reader = csv.DictReader(StringIO(csv_data))
        not_imported = []
        errors = []
        code_already_exists = []
        imported_codes = set()

        try:
            with transaction.atomic():
                for idx, row in enumerate(csv_reader):
                    code = row.get("code", None)
                    if not code:
                        self.logger.error(f"Row {idx + 1} does not contain a code. Skipping row. Row data: {row}")
                        continue
                    max_used = (
                        int(row["max_used"])
                        if row["max_used"] or not row["max_used"] == "" or row["max_used"].isdigit()
                        else 1
                    )
                    current_used = (
                        int(row["current_used"])
                        if row["current_used"] or not row["current_used"] == "" or row["current_used"].isdigit()
                        else 1
                    )
                    max_uses_per_user = (
                        int(row["max_uses_per_user"])
                        if row["max_uses_per_user"]
                        or not row["max_uses_per_user"] == ""
                        or row["max_uses_per_user"].isdigit()
                        else None
                    )
                    active_from = row["active_from"] or None
                    active_to = row["active_to"] or None
                    max_products_qty = row["max_products_qty"] or None
                    imported_codes.add(code)
                    if not code:
                        continue

                    if DiscountCode.objects.filter(code=code).exclude(rule=discount_rule).exists():
                        code_already_exists.append(code)
                        continue

                    # Validate current_used is less than or equal to max_used
                    if current_used > max_used:
                        self.logger.error(
                            f"Validation error for code {code}: current_used ({current_used}) is greater than max_used ({max_used})"
                        )
                        errors.append(code)
                        continue

                    try:
                        if overwrite_existing:
                            discount_code, created = DiscountCode.objects.update_or_create(
                                code=code,
                                rule=discount_rule,
                                defaults={
                                    "max_used": max_used,
                                    "current_used": current_used,
                                    "max_uses_per_user": max_uses_per_user,
                                    "active_from": active_from,
                                    "active_to": active_to,
                                    "max_products_qty": max_products_qty,
                                },
                            )
                            discount_code.save()
                        else:
                            created = False
                            try:
                                discount_code = DiscountCode.objects.get(code=code, rule=discount_rule)
                            except DiscountCode.DoesNotExist:
                                with transaction.atomic():
                                    discount_code = DiscountCode(
                                        code=code,
                                        rule=discount_rule,
                                        max_uses_per_user=max_uses_per_user,
                                        max_used=max_used,
                                        current_used=current_used,
                                        active_from=active_from,
                                        active_to=active_to,
                                        max_products_qty=max_products_qty,
                                    )
                                    discount_code.save()
                                created = True
                            if not created:
                                not_imported.append(code)

                    except IntegrityError as e:
                        self.logger.exception(e)
                        errors.append(code)
                    except Exception as e:
                        self.logger.exception(e)
                        errors.append(code)

                deleted_codes_list = None
                if delete_old:
                    deleted_codes = DiscountCode.objects.filter(rule=discount_rule).exclude(code__in=imported_codes)
                    deleted_codes_list = ", ".join([code.code for code in deleted_codes])
                    deleted_codes.delete()

        except IntegrityError as e:
            self.logger.exception("Transaction failed: %s", e)
            raise

        return not_imported, errors, deleted_codes_list, code_already_exists
