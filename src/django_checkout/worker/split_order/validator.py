# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import logging
from itertools import groupby

logger = logging.getLogger(__name__)


def check_that_order_can_be_split(cart: "Cart", channel: "Channel", skip_user_declaration=False):
    """
    Sprawdza, czy zamówienie może być podzielone na zamówienia częściowe.
    Zwraca items_to_split = ("<attribute__idx>", {'attribute__idx': 'csw', 'product__real_product__sku': 'MAX-36-Czarny'})
    """

    if (
        all([not channel.force_splitting_order_in_checkout, not cart.as_data.split_order, not skip_user_declaration])
        or not channel.feature_idxs_for_split_order
    ):
        return None, None
    try:
        from django_pim.models.feature import Feature, FeatureTypeEnum
        from django_pim.models.product_attribute import ProductAttribute
    except ImportError:
        logger.warning("To split order by attribute you need to install django_pim")
        return None, None

    feature = Feature.objects.filter(idx=channel.feature_idxs_for_split_order).first()
    feature_seperated = Feature.objects.filter(idx__in=channel.feature_separate_to_other_order)
    if not feature:
        return None, None

    if feature.feature_type == FeatureTypeEnum.SELECT:
        attr_value = "attribute__idx"
    elif feature.feature_type == FeatureTypeEnum.BOOL:
        attr_value = "value_bool"
    elif feature.feature_type == FeatureTypeEnum.DATETIME:
        attr_value = "value_datetime"
    else:
        logger.warning("Wrong feature type for splitting order. SELECT, DATETIME, BOOLEAN are supported.")
        return None, None

    rest_items = [item.sku for item in cart.as_data.cart.items]

    data_seperated = {}
    for feature_sep in feature_seperated:
        pa_data_seperated = ProductAttribute.objects.filter(
            product__real_product__sku__in=rest_items,
            feature=feature_sep,
            product__shop__idx=channel.idx,
        ).values("feature__idx", "product__real_product__sku")
        if not pa_data_seperated:
            continue
        data_seperated = {(True, feature_sep.idx): pa_data_seperated}
        items_seperated = [each["product__real_product__sku"] for each in pa_data_seperated]
        for item_to_remove in items_seperated:
            rest_items.remove(item_to_remove)

    data = ProductAttribute.objects.filter(
        product__real_product__sku__in=rest_items,
        feature__idx=channel.feature_idxs_for_split_order,
        product__shop__idx=channel.idx,
    ).values("feature__idx", attr_value, "product__real_product__sku")

    all_skus_without_attribute = [
        item.sku
        for item in cart.as_data.cart.items
        if item.sku not in [each["product__real_product__sku"] for each in data] and item.sku in rest_items
    ]

    attr_value_channel = channel.default_attr_split

    if len(attr_value_channel) == 0:
        attr_value_channel = None

    elif attr_value == "value_bool":
        attr_value_channel = channel.default_attr_split.capitalize()

    data_with_defaults = list(data) + [
        {
            attr_value: attr_value_channel,
            "feature__idx": channel.feature_idxs_for_split_order,
            "product__real_product__sku": sku,
        }
        for sku in all_skus_without_attribute
    ]

    key_func = lambda x: (str(x[attr_value]), x["feature__idx"])
    items_to_split = groupby(sorted(data_with_defaults, key=key_func), key=key_func)
    items_to_split = {key: list(group) for key, group in items_to_split}

    items_to_split.update(data_seperated)

    items_to_split_length = len(list(groupby(sorted(data_with_defaults, key=key_func), key=key_func))) + len(
        data_seperated
    )
    return items_to_split, items_to_split_length
