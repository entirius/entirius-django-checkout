# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""get_bundle_quantities: a bundle counts what its components allow, as the cart does, never its own Stock row."""

from unittest.mock import patch

import pytest

from django_checkout.models import ProductRepresentation, Stock, Supplier
from django_checkout.output.quantities import get_bundle_quantities

BUNDLE = "ENT-BND-SLEEP"
BED, PILLOW, LAMP = "ENT-BED", "ENT-PILLOW", "ENT-LAMP"


@pytest.fixture
def supplier(channel):
    return Supplier.objects.create(code="kestrel", name="Kestrel Supply", is_global=True, channel=channel)


@pytest.fixture
def stock(channel, supplier):
    def put(sku, quantity):
        product = ProductRepresentation.objects.create(sku=sku, channel=channel)
        return Stock.objects.create(product=product, supplier=supplier, quantity=quantity)

    return put


@pytest.fixture
def bundle(db, channel, default_language, default_currency):
    """The bundle: one bed (required), two pillows and a lamp (optional, not default)."""
    from django_pim.models import BundleLink, BundleSection, FeatureSet, Product, ProductBundle, RealProduct
    from django_pim.models import Channel as PimChannel

    shop = PimChannel.objects.create(
        idx=channel.idx, name="Novatrade", default_language=default_language, default_currency=default_currency
    )
    feature_set = FeatureSet.objects.create(idx="default", name="Default")

    def product(sku, model=Product):
        return model.objects.create(
            real_product=RealProduct.objects.create(sku=sku), shop=shop, feature_set=feature_set
        )

    the_bundle = product(BUNDLE, ProductBundle)
    section = BundleSection.objects.create(idx="included")
    for sku, quantity, required in ((BED, 1, True), (PILLOW, 2, False), (LAMP, 1, False)):
        BundleLink.objects.create(
            product_bundle=the_bundle,
            subproduct=product(sku),
            quantity=quantity,
            section=section,
            is_required=required,
            is_default=False,
            can_change_quantity=not required,
        )
    return the_bundle


def test_the_components_decide_not_the_bundle_row(channel, stock, bundle):
    stock(BUNDLE, 15)
    stock(BED, 3)
    stock(PILLOW, 4)
    stock(LAMP, 10)

    # Pillows come two to a bundle: 4 of them cover 2 bundles.
    assert get_bundle_quantities([BUNDLE], channel.idx) == {BUNDLE: 2}


def test_a_component_out_of_stock_sells_none(channel, stock, bundle):
    stock(BUNDLE, 15)
    stock(BED, 0)
    stock(PILLOW, 4)
    stock(LAMP, 10)

    assert get_bundle_quantities([BUNDLE], channel.idx) == {BUNDLE: 0}


def test_with_limits_only_required_and_default_components_count(channel, stock, bundle):
    stock(BUNDLE, 15)
    stock(BED, 3)
    stock(PILLOW, 0)
    stock(LAMP, 0)

    with patch("django_checkout.domain.quantities._fetch_bundle_limits", return_value={BUNDLE: {"max": 5}}):
        assert get_bundle_quantities([BUNDLE], channel.idx) == {BUNDLE: 3}


def test_other_skus_are_left_out(channel, stock, bundle):
    stock(BED, 3)

    assert get_bundle_quantities([BED, "ENT-UNKNOWN"], channel.idx) == {}


def test_an_unknown_channel_or_no_skus_answers_nothing(channel, bundle):
    assert get_bundle_quantities([BUNDLE], "no-such-channel") == {}
    assert get_bundle_quantities([], channel.idx) == {}
