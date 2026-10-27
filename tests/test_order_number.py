# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Order numbers (``in_channel_id``) under concurrent orders.

The number is ``MAX(in_channel_id) + 1`` read without a lock: two orders of one channel placed at the
same moment read the same MAX, and the second insert used to fail on ``in_channel_id_unique_per_channel``
(an IntegrityError, a 500 to the storefront). The insert now retries with the next number on that
collision. The channel row is never locked: ``Order.create`` calls the payment gateway inside its
transaction, so a lock would be held across that HTTP call.
"""

import threading
import time
from unittest import mock

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from django_checkout.models import Channel, Order
from django_checkout.models.order import ORDER_NUMBER_ATTEMPTS


def _order(channel, **fields):
    return Order.objects.create(channel=channel, order_body={}, **fields)


@pytest.mark.django_db
def test_numbers_follow_each_other_per_channel(channel, other_channel):
    assert [_order(channel).in_channel_id for _ in range(3)] == [1, 2, 3]
    assert _order(other_channel).in_channel_id == 1


@pytest.mark.django_db(transaction=True)
def test_an_order_placed_while_another_is_uncommitted_gets_the_next_number(channel):
    inserted = threading.Event()
    channel_checked = threading.Event()
    numbers: dict[str, int] = {}
    errors: list[Exception] = []
    channel_lock: list[str] = []

    def first():
        try:
            with transaction.atomic():
                numbers["first"] = _order(channel).in_channel_id
                inserted.set()
                channel_checked.wait(timeout=5)
                # The second order is being placed now; it reads the same MAX and has to retry.
                time.sleep(0.5)
        except Exception as error:  # noqa: BLE001 — reported by the assertions below
            errors.append(error)
        finally:
            connection.close()

    def second():
        try:
            inserted.wait(timeout=5)
            numbers["second"] = _order(channel).in_channel_id
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    def lock_channel():
        try:
            inserted.wait(timeout=5)
            with transaction.atomic():
                list(Channel.objects.select_for_update(nowait=True).filter(pk=channel.pk))
            channel_lock.append("free")
        except DatabaseError:
            channel_lock.append("locked")
        finally:
            channel_checked.set()
            connection.close()

    threads = [threading.Thread(target=fn) for fn in (first, second, lock_channel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert numbers == {"first": 1, "second": 2}
    assert channel_lock == ["free"]


@pytest.mark.django_db
def test_another_integrity_error_is_not_retried(channel):
    with mock.patch.object(Order, "next_in_channel_id", wraps=Order.next_in_channel_id) as next_number:
        with pytest.raises(IntegrityError):
            Order.objects.create(channel=channel, order_body=None)
    assert next_number.call_count == 1


@pytest.mark.django_db
def test_the_retry_gives_up_after_a_few_collisions(channel):
    _order(channel)
    with mock.patch.object(Order, "next_in_channel_id", return_value=1) as next_number:
        with pytest.raises(IntegrityError):
            _order(channel)
    assert next_number.call_count == ORDER_NUMBER_ATTEMPTS
