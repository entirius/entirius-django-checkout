# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import logging

from django.db.utils import IntegrityError
from django.utils import timezone
from django_pim.models import Attribute, Feature, Product, ProductCategory

from django_checkout.models import (
    Channel,
    DiscountCode,
    DiscountModeOfAction,
    DiscountModeOfActionType,
    DiscountRuleCode,
    ShippingMethod,
)
from django_checkout.models.discount_rule_code import ModifiersForDiscountRule
from django_checkout.utils.importer import better_read_from_csv

logger = logging.getLogger(__name__)


def creating_in_db_for_each_line(data):
    error_rows = 0
    for row in data:
        try:
            extra_value = row["extra_value"]
            channels = Channel.objects.filter(idx__in=row["channels"].split(","))
            if row["modifier"] not in [
                ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT,
                ModifiersForDiscountRule.STEP_PRICE_PERCENT_DISCOUNT,
                ModifiersForDiscountRule.STEP_QTY_PRICE_DISCOUNT_WHOLE_CART,
                ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART,
                ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY,
                ModifiersForDiscountRule.GRATIS_STEPPED,
                ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
            ]:
                extra_value = int(extra_value)
            else:
                extra_value = json.loads(extra_value.replace("'", '"'))
            rule, is_created = DiscountRuleCode.objects.get_or_create(
                name=row["name"],
                defaults={
                    "free_shipping": row["free_shipping"],
                    "min_order_amount": row["min_order_amount"],
                    "free_order": row["free_order"],
                    "is_omnibus": row["is_omnibus"],
                    "target": row["target"],
                    "modifier": row["modifier"],
                    "priority": row["priority"],
                    "combine_with_other_rules": row["combine_with_other_rules"],
                    "automatic_applications": row["automatic_applications"],
                    "created_at": row["created_at"],
                    "extra_value": extra_value,
                },
            )
            rule.channels.set(channels)
            try:
                rule.save()
                free_shipping_methods_codes = (
                    [] if row["free_shipping_methods"] is None else row["free_shipping_methods"].split(",")
                )
                rule.free_shipping_methods.set(ShippingMethod.objects.filter(code__in=free_shipping_methods_codes))
            except IntegrityError as e:
                error_rows += 1
                logger.exception(e)
                continue
            n = 1
            is_more_modes_of_action = True
            while is_more_modes_of_action:
                try:
                    if not row.get(f"is_inclusion_or_exclusion_{n}", None):
                        continue

                    mode_of_action = DiscountModeOfAction(
                        rule=rule,
                        take_common_part=row[f"take_common_part_{n}"] if row[f"take_common_part_{n}"] else False,
                        product_price_to=row[f"product_price_to_{n}"],
                        product_price_from=row[f"product_price_from_{n}"],
                        cart_price_to=row[f"cart_price_to_{n}"],
                        cart_price_from=row[f"cart_price_from_{n}"],
                        qty_to=row[f"qty_to_{n}"],
                        qty_from=row[f"qty_from_{n}"],
                        cart_qty_to=row[f"cart_qty_to_{n}"],
                        cart_qty_from=row[f"cart_qty_from_{n}"],
                        is_inclusion_or_exclusion=(
                            row[f"is_inclusion_or_exclusion_{n}"]
                            if row[f"is_inclusion_or_exclusion_{n}"]
                            else DiscountModeOfActionType.INCLUSION
                        ),
                    )
                    mode_of_action.save()
                    attributes_idxs = [] if row[f"attributes_{n}"] is None else row[f"attributes_{n}"].split(",")
                    features_qty_greater_than_attr_value_idxs = (
                        []
                        if row[f"features_qty_greater_than_attr_value_{n}"] is None
                        else row[f"features_qty_greater_than_attr_value_{n}"].split(",")
                    )
                    categories_idxs = [] if row[f"categories_{n}"] is None else row[f"categories_{n}"].split(",")
                    product_skus = [] if row[f"products_{n}"] is None else row[f"products_{n}"].split(",")

                    if attributes_idxs:
                        mode_of_action.attributes.set(Attribute.objects.filter(idx__in=attributes_idxs))
                    if features_qty_greater_than_attr_value_idxs:
                        mode_of_action.features_qty_greater_than_attr_value.set(
                            Feature.objects.filter(idx__in=features_qty_greater_than_attr_value_idxs)
                        )
                    if categories_idxs:
                        mode_of_action.categories.set(ProductCategory.objects.filter(idx__in=categories_idxs))
                    if product_skus:
                        mode_of_action.products.set(Product.objects.filter(real_product__sku__in=product_skus))
                    mode_of_action.save()
                    n += 1

                except KeyError:
                    is_more_modes_of_action = False
                except Exception as e:
                    is_more_modes_of_action = False
                    logger.exception(e)

            n = 1
            is_more_codes = True
            while is_more_codes:
                try:
                    if row[f"code_{n}"] is None:
                        n += 1
                    else:
                        code = DiscountCode(
                            rule=rule,
                            code=row[f"code_{n}"],
                            max_used=row[f"max_used_{n}"],
                            max_uses_per_user=row[f"max_uses_per_user_{n}"],
                            current_used=row[f"current_used_{n}"],
                            active_from=row[f"active_from_{n}"],
                            active_to=row[f"active_to_{n}"],
                            max_products_qty=row[f"max_products_qty_{n}"],
                        )
                        code.active_from = (
                            None
                            if row[f"active_from_{n}"] is None
                            else timezone.datetime.strptime(row[f"active_from_{n}"], "%Y-%m-%d").date()
                        )
                        code.active_to = (
                            None
                            if row[f"active_to_{n}"] is None
                            else timezone.datetime.strptime(row[f"active_to_{n}"], "%Y-%m-%d").date()
                        )
                        code.save()
                        n += 1
                except KeyError:
                    is_more_codes = False
                except Exception as e:
                    is_more_codes = False
                    logger.exception(e)
        except Exception as e:
            logger.exception(e)
    return error_rows, len(data)


def import_discount_rules_from_csv(filepath=None, file=None):
    row, msg = better_read_from_csv(filepath, file)
    try:
        errors, number_discount_rules_csv = creating_in_db_for_each_line(row)
    except Exception as e:
        logger.exception(e)
        raise Exception(e)
    return errors, number_discount_rules_csv
