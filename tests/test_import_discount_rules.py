# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""``import-discount-rules`` (and the admin CSV import): one CSV row per rule, ``code_<n>`` columns for its codes."""

import signal

import pytest

from django_checkout.models import DiscountCode, DiscountRuleCode
from django_checkout.worker.import_discount_rules import creating_in_db_for_each_line


class _Hung(BaseException):
    """Not an ``Exception``: the importer's ``except Exception`` must not swallow it."""


@pytest.fixture(autouse=True)
def no_hang():
    def hung(*args):
        raise _Hung("the import did not finish in 10 s")

    previous = signal.signal(signal.SIGALRM, hung)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def _row(channel, *codes: str, name: str = "Imported") -> dict:
    row = {
        "name": name,
        "free_shipping": False,
        "min_order_amount": "0",
        "free_order": False,
        "is_omnibus": False,
        "target": "all",
        "modifier": "percent_discount",
        "priority": 1,
        "combine_with_other_rules": False,
        "automatic_applications": False,
        "created_at": "2026-01-01T00:00:00Z",
        "extra_value": "10",
        "channels": channel.idx,
        "free_shipping_methods": None,
    }
    for n, code in enumerate(codes, start=1):
        row |= {
            f"code_{n}": code,
            f"max_used_{n}": 1,
            f"max_uses_per_user_{n}": None,
            f"current_used_{n}": 0,
            f"active_from_{n}": None,
            f"active_to_{n}": None,
            f"max_products_qty_{n}": None,
        }
    return row


@pytest.mark.django_db
def test_a_row_without_a_mode_of_action_imports_its_codes(channel):
    """The mode-of-action loop never ended once the row ran out of modes: the import hung before any code."""
    creating_in_db_for_each_line([_row(channel, "SUMMER")])

    rule = DiscountRuleCode.objects.get(name="Imported")
    assert list(rule.codes.values_list("code", flat=True)) == ["SUMMER"]


@pytest.mark.django_db
def test_a_row_with_a_mode_of_action_imports_it_and_its_codes(channel):
    row = _row(channel, "SUMMER")
    row |= {
        "is_inclusion_or_exclusion_1": "inclusion",
        "take_common_part_1": None,
        **{f"{field}_1": None for field in ("product_price_to", "product_price_from", "cart_price_to")},
        **{f"{field}_1": None for field in ("cart_price_from", "qty_to", "qty_from", "cart_qty_to", "cart_qty_from")},
        **{f"{field}_1": None for field in ("attributes", "features_qty_greater_than_attr_value", "categories")},
        "products_1": None,
    }

    creating_in_db_for_each_line([row])

    rule = DiscountRuleCode.objects.get(name="Imported")
    assert rule.discount_mode_of_actions_rule.count() == 1
    assert rule.codes.count() == 1


@pytest.mark.django_db
def test_a_code_another_rule_holds_in_the_channel_is_skipped_and_the_others_imported(channel):
    holder = DiscountRuleCode.objects.create(name="Holder", modifier="percent_discount", extra_value=5)
    holder.channels.set([channel])
    DiscountCode.objects.create(rule=holder, code="SUMMER")

    creating_in_db_for_each_line([_row(channel, "SUMMER", "WINTER")])

    imported = DiscountRuleCode.objects.get(name="Imported")
    assert list(imported.codes.values_list("code", flat=True)) == ["WINTER"]
    assert list(DiscountCode.objects.filter(code="SUMMER").values_list("rule_id", flat=True)) == [holder.pk]


@pytest.mark.django_db
def test_importing_the_same_file_twice_adds_no_code(channel):
    creating_in_db_for_each_line([_row(channel, "SUMMER")])
    creating_in_db_for_each_line([_row(channel, "SUMMER")])

    assert DiscountCode.objects.filter(code="SUMMER").count() == 1


@pytest.mark.django_db
def test_moving_a_rule_into_a_channel_where_its_code_is_taken_skips_the_row(channel, other_channel):
    holder = DiscountRuleCode.objects.create(name="Holder", modifier="percent_discount", extra_value=5)
    holder.channels.set([channel])
    DiscountCode.objects.create(rule=holder, code="SUMMER")
    creating_in_db_for_each_line([_row(other_channel, "SUMMER")])
    moved = _row(other_channel, "SUMMER")
    moved["channels"] = f"{other_channel.idx},{channel.idx}"

    creating_in_db_for_each_line([moved])

    imported = DiscountRuleCode.objects.get(name="Imported")
    assert list(imported.channels.values_list("idx", flat=True)) == [other_channel.idx]
