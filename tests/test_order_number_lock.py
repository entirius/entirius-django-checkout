# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Order numbers (``in_channel_id``) under concurrent orders.

The number was ``MAX(in_channel_id) + 1`` read without a lock: two orders of one channel placed at the
same moment read the same MAX, and the second insert failed on ``in_channel_id_unique_per_channel``
(an IntegrityError, a 500 to the storefront). The channel row is now locked while the number is taken,
in the transaction of the insert.
"""

import threading
import time

import pytest
from django.db import connection, transaction

from django_checkout.models import Order


def _order(channel, **fields):
    return Order.objects.create(channel=channel, order_body={}, **fields)


@pytest.mark.django_db
def test_numbers_follow_each_other_per_channel(channel, other_channel):
    assert [_order(channel).in_channel_id for _ in range(3)] == [1, 2, 3]
    assert _order(other_channel).in_channel_id == 1


@pytest.mark.django_db(transaction=True)
def test_an_order_placed_while_another_takes_its_number_gets_the_next_one(channel):
    numbered = threading.Barrier(2)
    numbers: list[int] = []
    errors: list[Exception] = []

    def first():
        try:
            with transaction.atomic():
                number = Order.next_in_channel_id(channel)
                numbered.wait()
                # The second order is being placed now; it has to wait for this insert.
                time.sleep(0.5)
                numbers.append(_order(channel, in_channel_id=number).in_channel_id)
        except Exception as error:  # noqa: BLE001 — reported by the assertions below
            errors.append(error)
        finally:
            connection.close()

    def second():
        try:
            numbered.wait()
            numbers.append(_order(channel).in_channel_id)
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert sorted(numbers) == [1, 2]
