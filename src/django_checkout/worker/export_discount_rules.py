# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import csv
import logging
import os
from itertools import chain

import pytz
from django.utils import timezone

from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType

from .. import settings

logger = logging.getLogger(__name__)

dicsount_rule_fields = [
    "channels",
    "name",
    "free_shipping",
    "free_shipping_methods",
    "min_order_amount",
    "free_order",
    "is_omnibus",
    "target",
    "modifier",
    "extra_value",
    "priority",
    "combine_with_other_rules",
    "automatic_applications",
    "created_at",
]

mode_of_action_fields = [
    "attributes",
    "features_qty_greater_than_attr_value",
    "categories",
    "products",
    "take_common_part",
    "product_price_to",
    "product_price_from",
    "cart_price_to",
    "cart_price_from",
    "qty_to",
    "qty_from",
    "cart_qty_to",
    "cart_qty_from",
    "is_inclusion_or_exclusion",
]

discount_code_fields = [
    "code",
    "max_used",
    "max_uses_per_user",
    "current_used",
    "active_from",
    "active_to",
    "max_products_qty",
]


def ensure_dir_exists(path_dir, quiet=False):
    if os.path.isdir(path_dir):
        return True
    if not quiet:
        logger.info(f'"{path_dir}" directory does not exists, creating')
    os.makedirs(path_dir)


def write_rows(writer, discount_rules):
    writer.writeheader()
    for discount_rule in discount_rules:
        row = {}
        discount_mode_of_actions_rule = discount_rule.discount_mode_of_actions_rule.all()
        codes = discount_rule.codes.all()
        for idx, discount_mode_of_action in enumerate(discount_mode_of_actions_rule):
            for col in mode_of_action_fields:
                if col == "attributes":
                    row[f"{col}_{idx + 1}"] = ",".join(
                        discount_mode_of_action.attributes.all().values_list("idx", flat=True)
                    )
                elif col == "features_qty_greater_than_attr_value":
                    row[f"{col}_{idx + 1}"] = ",".join(
                        discount_mode_of_action.features_qty_greater_than_attr_value.all().values_list("idx", flat=True)
                    )
                elif col == "categories":
                    row[f"{col}_{idx + 1}"] = ",".join(
                        discount_mode_of_action.categories.all().values_list("idx", flat=True)
                    )
                elif col == "products":
                    row[f"{col}_{idx + 1}"] = ",".join(
                        discount_mode_of_action.products.all().values_list("real_product__sku", flat=True)
                    )
                elif col == "is_inclusion_or_exclusion":
                    if discount_mode_of_action.is_inclusion_or_exclusion is not None:
                        row[f"{col}_{idx + 1}"] = discount_mode_of_action.is_inclusion_or_exclusion
                    elif discount_mode_of_action.is_inclusion_or_exclusion is None:
                        row[f"{col}_{idx + 1}"] = DiscountModeOfActionType.INCLUSION
                else:
                    row[f"{col}_{idx + 1}"] = getattr(discount_mode_of_action, col, None)

        for idx, code in enumerate(codes):
            for col in discount_code_fields:
                row[f"{col}_{idx + 1}"] = getattr(code, col, None)

        for col in dicsount_rule_fields:
            if col == "channels":
                row[f"{col}"] = ",".join(list(discount_rule.channels.all().values_list("idx", flat=True)))
            elif col == "free_shipping_methods":
                row[f"{col}"] = ",".join(
                    [shipping_method.code for shipping_method in discount_rule.free_shipping_methods.all()]
                )
            else:
                row[f"{col}"] = getattr(discount_rule, col, None)

        writer.writerow(row)


def export_discount_rules_to_csv(discount_rules, response=None):
    discount_rules = discount_rules.prefetch_related("discount_mode_of_actions_rule")
    discount_rules = discount_rules.prefetch_related("codes")
    max_discount_mode_of_actions = max_codes = []

    for discount_rule in discount_rules:
        max_discount_mode_of_actions.append(len(discount_rule.discount_mode_of_actions_rule.all()))
        max_codes.append(len(discount_rule.codes.all()))

    max_codes = [1] if not max_codes else max_codes
    max_discount_mode_of_actions = [1] if not max_discount_mode_of_actions else max_discount_mode_of_actions

    summary_fields = [
        *dicsount_rule_fields,
        *chain.from_iterable(
            [
                [f"{field}_{number}" for field in mode_of_action_fields]
                for number in range(1, max(max_discount_mode_of_actions) + 1)
            ]
        ),
        *chain.from_iterable(
            [[f"{field}_{number}" for field in discount_code_fields] for number in range(1, max(max_codes) + 1)]
        ),
    ]

    if response:
        writer = csv.DictWriter(response, fieldnames=summary_fields, delimiter=",")
        write_rows(writer, discount_rules)
    else:
        utc_tz = pytz.timezone("UTC")
        now = timezone.now().replace(tzinfo=utc_tz)
        import_started_at = now.astimezone(pytz.timezone("Europe/Warsaw"))
        export_package_dir_folder = os.path.join(
            settings.EXPORT_DIR,
            "discount_rules",
            "{}-{}".format("discount_rules", import_started_at.strftime("%Y%m%d-%H%M%S")),
        )
        ensure_dir_exists(export_package_dir_folder)
        filepath = os.path.join(export_package_dir_folder, "discount_rules.csv")
        with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=summary_fields, delimiter=",")
            write_rows(writer, discount_rules)
