# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Variant lines of order e-mail items ("Size: M, Colour: Red"), read from matrix where it is installed."""

from collections import defaultdict

from django.apps import apps


def variant_lines(skus: list[str], channel_idx: str, language: str) -> dict[str, str]:
    """Variant line by SKU in one query; empty without matrix or without variant attributes."""
    if not skus or not apps.is_installed("django_matrix"):
        return {}
    from django_matrix.models import Attribute

    rows = Attribute.objects.filter(product__sku__in=skus, product__channel__idx=channel_idx, linking=True).values_list(
        "product__sku", "feature_name_t9n", "attribute_value_text", "attribute_value_t9n"
    )
    pairs = defaultdict(list)
    for sku, name_t9n, value_text, value_t9n in rows.order_by("product__sku", "pk"):
        label, value = _localized(name_t9n, language), value_text or _localized(value_t9n, language)
        if label and value:
            pairs[sku].append(f"{label}: {value}")
    return {sku: ", ".join(items) for sku, items in pairs.items()}


def _localized(t9n: object, language: str) -> str:
    if not isinstance(t9n, dict):
        return ""
    return str(t9n.get(language) or "")
