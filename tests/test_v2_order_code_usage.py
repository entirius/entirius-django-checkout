# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A v2 order counts the discount codes it takes, as a v1 order does.

v2 ``POST orders/`` never called ``use_discounts``: ``current_used`` stayed at 0 and no ``UsedCoupon`` row was
written, so ``max_used`` and ``max_uses_per_user`` limited nothing for v2 orders. A one-use code worked forever.

Both order paths now count through one service function, in the order's transaction, with a conditional update:
two orders racing for the last use cannot both take it. A v2 order whose code no longer applies (used up between
the cart and the order, or taken by a concurrent order) is refused with a 409 that names the reason, instead of
being placed at a total the customer never saw.

Views are called directly via ``APIRequestFactory`` (the module has no ROOT_URLCONF).
"""

import json
import threading
import time
from dataclasses import asdict
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.db.models import F
from django.test import RequestFactory
from django_regional.models import Currency
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.views.cart_views import CartDiscountsView
from django_checkout.api.v2.views.order_views import OrderCreateView
from django_checkout.domain.dto.discount import ValidatedDiscountData
from django_checkout.enums import CartStatus, ItemStatus, OrderStatus, PaymentProvider
from django_checkout.models import (
    DiscountCode,
    DiscountRuleCode,
    ModifiersForDiscountRule,
    Order,
    PaymentMethod,
    ShippingMethod,
    ShippingOption,
    UsedCoupon,
)
from django_checkout.services import cart_service, order_service
from django_checkout.utils import sanitize
from django_checkout.views import order as v1_orders
from django_checkout.views.order import order_view, use_discounts
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    TRIGGER_SKU,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)

factory = APIRequestFactory()
CODE = "WELCOME10"
GUEST = "buyer@novatrade.test"


@pytest.fixture(autouse=True)
def v2_errors(settings):
    """The service's v2 handler formats the 409, as in a deploy."""
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
    }


@pytest.fixture
def methods(channel):
    """A courier and a bank transfer for PL / EUR: what a cart needs to become an order."""
    courier = ShippingMethod.objects.create(channel=channel, code="courier", name_t9n={"en": "Courier"})
    # ShippingOption.save() calls cache.delete_pattern() (django-redis); the test cache lacks it.
    with patch("django_checkout.models.shipping_option.cache"):
        ShippingOption.objects.create(
            method=courier,
            currency=channel.default_currency,
            country_code="ALL",
            price_brutto=Decimal("10.00"),
            tax_rate=Decimal("0.23"),
        )
    transfer = PaymentMethod.objects.create(
        channel=channel, code="transfer", provider=PaymentProvider.TRANSFER, name_t9n={"en": "Bank transfer"}
    )
    transfer.countries.add(channel.default_country)
    transfer.currencies.add(channel.default_currency)


@pytest.fixture
def shop(channel, priced_catalog, coffee_in_stock, methods):  # noqa: F811
    """A kilo of coffee (123.00 EUR) in stock, a shipping and a payment method."""
    return channel


def code_rule(channel, code=CODE, *, currency="EUR", automatic=False, **limits):
    """A 10 % rule of the channel in one currency with one code; ``limits`` go to the code row."""
    rule = DiscountRuleCode.objects.create(
        name=f"{code}_{currency}",
        modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT,
        extra_value=10,
        min_order_amount=Decimal("0"),
        automatic_applications=automatic,
    )
    rule.channels.add(channel)
    rule.currencies.add(Currency.objects.get_or_create(iso3=currency, defaults={"name_en": currency})[0])
    return DiscountCode.objects.create(rule=rule, code=code, **{"max_used": 100, **limits})


def address(email):
    return {
        "email": email,
        "firstname": "Jan",
        "lastname": "Kowalski",
        "country_code": "PL",
        "city": "Warsaw",
        "postcode": "00-001",
        "street": "Main 1",
        "dialling_code": "+48",
        "telephone": "123456789",
    }


def ready_cart(channel, email=GUEST, customer=None, codes=(CODE,)):
    """A coffee cart with addresses, the courier and the transfer, then the codes: ready to become an order."""
    record, _ = cart_service.create_cart(
        channel=channel,
        items=[{"sku": TRIGGER_SKU, "quantity": 1}],
        currency_code="EUR",
        country_code="PL",
        customer=customer,
    )
    cart_id = str(record.cart_id)
    cart_service.patch_addresses(channel, cart_id, address(email), address(email), customer=customer)
    cart_service.patch_shipping(channel, cart_id, "courier", customer=customer)
    cart_service.patch_payment(channel, cart_id, "transfer", customer=customer)
    record, _ = cart_service.patch_discounts(channel, cart_id, [{"code": c} for c in codes], customer=customer)
    return record


def applied(cart):
    cart.refresh_from_db()
    return [d.code for d in cart.as_data.cart.discounts]


def call(view, request, user=None, **kwargs):
    if user is not None:
        force_authenticate(request, user=user)
    return view.as_view()(request, **kwargs)


def place_order(api_key, channel, cart, user=None):
    request = factory.post("/orders/", {"cart_id": str(cart.cart_id)}, format="json", HTTP_X_API_KEY=api_key.key)
    return call(OrderCreateView, request, user, channel_idx=channel.idx)


def send_codes(api_key, channel, cart, *codes, user=None) -> list[str]:
    """PATCH the cart's discounts; the v2 codes of the per-code rejections."""
    request = factory.patch("/", {"codes": [{"code": c} for c in codes]}, format="json", HTTP_X_API_KEY=api_key.key)
    response = call(CartDiscountsView, request, user, channel_idx=channel.idx, cart_id=str(cart.cart_id))
    assert response.status_code == 200
    return [e["code"] for e in response.data.get("_errors", []) if e["field"] and e["field"].startswith("codes[")]


def used(code_row) -> int:
    code_row.refresh_from_db()
    return code_row.current_used


def complete(order_id):
    Order.objects.get(order_id=order_id).modify_status(OrderStatus.COMPLETED)


# --- Counting -------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_v2_order_counts_its_code_on_the_row_the_cart_applied(api_key, shop):
    eur, pln = code_rule(shop, currency="EUR"), code_rule(shop, currency="PLN")
    cart = ready_cart(shop)
    assert applied(cart) == [CODE]

    response = place_order(api_key, shop, cart)

    assert response.status_code == 201
    assert (used(eur), used(pln)) == (1, 0)
    order = Order.objects.get()
    assert list(UsedCoupon.objects.values_list("order", "discount_code", "billing_email")) == [(order.pk, CODE, GUEST)]


@pytest.mark.django_db
def test_a_used_up_code_is_refused_in_the_next_v2_cart(api_key, shop):
    code_rule(shop, max_used=1)
    assert place_order(api_key, shop, ready_cart(shop)).status_code == 201

    other = ready_cart(shop, email="other@novatrade.test", codes=())

    assert send_codes(api_key, shop, other, CODE) == ["DISCOUNT_USAGE_LIMIT_REACHED"]
    assert applied(other) == []


# --- Refused orders -------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_code_used_up_between_the_cart_and_the_order_refuses_the_order(api_key, shop):
    """The cart showed the discount; another order took the last use before this one was placed."""
    code = code_rule(shop, max_used=1)
    cart = ready_cart(shop)
    assert applied(cart) == [CODE]
    DiscountCode.objects.filter(pk=code.pk).update(current_used=1)

    response = place_order(api_key, shop, cart)

    assert response.status_code == 409
    assert response.data["error"] == "DISCOUNT_USAGE_LIMIT_REACHED"
    assert CODE in response.data["message"]
    assert response.data["debug_id"]
    assert not Order.objects.exists()
    assert applied(cart) == [CODE]  # the cart is left for the customer to review


@pytest.mark.django_db
def test_after_the_refusal_the_next_discounts_write_drops_the_code_and_the_order_goes_through(api_key, shop):
    code = code_rule(shop, max_used=1)
    cart = ready_cart(shop)
    DiscountCode.objects.filter(pk=code.pk).update(current_used=1)
    assert place_order(api_key, shop, cart).status_code == 409

    assert send_codes(api_key, shop, cart, CODE) == ["DISCOUNT_USAGE_LIMIT_REACHED"]
    assert applied(cart) == []
    assert place_order(api_key, shop, cart).status_code == 201
    assert Order.objects.get().order_body["total"] == "133.00"  # 123.00 coffee + 10.00 courier, no discount


@pytest.mark.django_db
def test_the_last_use_taken_while_the_order_is_placed_refuses_it(api_key, shop):
    """The order-time check passed; a concurrent order committed the last use before this one counted it."""
    code = code_rule(shop, max_used=1)
    cart = ready_cart(shop)
    real_process_cart = order_service.process_cart

    def another_order_takes_the_last_use(*args, **kwargs):
        result = real_process_cart(*args, **kwargs)
        DiscountCode.objects.filter(pk=code.pk).update(current_used=F("max_used"))
        return result

    with patch.object(order_service, "process_cart", side_effect=another_order_takes_the_last_use):
        response = place_order(api_key, shop, cart)

    assert response.status_code == 409
    assert response.data["error"] == "DISCOUNT_USAGE_LIMIT_REACHED"
    assert not Order.objects.exists()  # rolled back with the code use it could not take
    assert not UsedCoupon.objects.exists()
    assert used(code) == 1
    cart.refresh_from_db()
    assert cart.cart_status == CartStatus.NEW  # still open: the customer can review it and order again


@pytest.mark.django_db
def test_an_automatic_rule_at_its_code_limit_does_not_refuse_the_order(api_key, shop):
    """The cart applies automatic rules whatever their codes' counters say, so the order must not refuse them."""
    code = code_rule(shop, "AUTO10", automatic=True, max_used=1, current_used=1)
    cart = ready_cart(shop, codes=())
    assert applied(cart) == ["AUTO10"]

    response = place_order(api_key, shop, cart)

    assert response.status_code == 201
    assert used(code) == 1


# --- Per customer ---------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_max_uses_per_user_limits_a_logged_in_customer(api_key, shop, customer):
    """As in v1: uses are counted per e-mail address, from orders that reached a final status."""
    code_rule(shop, max_uses_per_user=1)
    email = customer.user.email
    first = place_order(api_key, shop, ready_cart(shop, email, customer), user=customer.user)
    assert first.status_code == 201
    complete(first.data["order_id"])

    again = ready_cart(shop, email, customer, codes=())

    assert send_codes(api_key, shop, again, CODE, user=customer.user) == ["DISCOUNT_PER_USER_LIMIT_REACHED"]


@pytest.mark.django_db
def test_max_uses_per_user_limits_a_guest_by_the_address_email(api_key, shop):
    code_rule(shop, max_uses_per_user=1)
    first = place_order(api_key, shop, ready_cart(shop, GUEST))
    assert first.status_code == 201
    complete(first.data["order_id"])

    same_email, other_email = ready_cart(shop, GUEST, codes=()), ready_cart(shop, "other@novatrade.test", codes=())

    assert send_codes(api_key, shop, same_email, CODE) == ["DISCOUNT_PER_USER_LIMIT_REACHED"]
    assert send_codes(api_key, shop, other_email, CODE) == []


@pytest.mark.django_db
def test_a_cart_that_reached_the_per_user_limit_after_it_was_shown_refuses_the_order(api_key, shop):
    """The customer's earlier order with the code completed while this cart waited."""
    code_rule(shop, max_uses_per_user=1)
    earlier = place_order(api_key, shop, ready_cart(shop))
    waiting = ready_cart(shop)
    assert applied(waiting) == [CODE]
    complete(earlier.data["order_id"])

    response = place_order(api_key, shop, waiting)

    assert response.status_code == 409
    assert response.data["error"] == "DISCOUNT_PER_USER_LIMIT_REACHED"
    assert Order.objects.count() == 1


# --- v1 -------------------------------------------------------------------------------------------------------------


def v1_order(api_key, channel, cart):
    request = RequestFactory().post(
        "/orders/", json.dumps({"cart_id": str(cart.cart_id)}), content_type="application/json"
    )
    request.META["HTTP_X_API_KEY"] = api_key.key
    response = order_view(request, channel_idx=channel.idx)
    return response.status_code, json.loads(response.content)


@pytest.mark.django_db(transaction=True)
def test_a_v1_order_with_an_automatic_rule_at_its_code_limit_is_placed(api_key, shop):
    """v1 counted the automatic rule's code past ``max_used``: the usage constraint failed after the order was
    committed and the customer got 403 for an order that existed. The use is no longer taken past the limit."""
    code = code_rule(shop, "AUTO10", automatic=True, max_used=1, current_used=1)
    cart = ready_cart(shop, codes=())

    status, _ = v1_order(api_key, shop, cart)

    assert status == 201
    assert Order.objects.count() == 1
    assert used(code) == 1


@pytest.mark.django_db
def test_a_v1_order_still_counts_its_code(api_key, shop):
    code = code_rule(shop)

    status, _ = v1_order(api_key, shop, ready_cart(shop))

    assert status == 201
    assert used(code) == 1
    assert UsedCoupon.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_v1_order_that_lost_the_last_use_is_not_saved(api_key, shop):
    """v1 counted after the order was committed: the update broke the usage constraint and the customer got 403
    for an order that existed. The order and its code use now commit together.

    ``transaction=True``: the order commits as in production, so the test sees an order the view failed to undo.
    """
    code = code_rule(shop, max_used=1)
    cart = ready_cart(shop)
    real_process_cart = v1_orders.process_cart

    def another_order_takes_the_last_use(*args, **kwargs):
        result = real_process_cart(*args, **kwargs)
        DiscountCode.objects.filter(pk=code.pk).update(current_used=F("max_used"))
        return result

    with patch.object(v1_orders, "process_cart", side_effect=another_order_takes_the_last_use):
        status, _ = v1_order(api_key, shop, cart)

    assert status == 403
    assert not Order.objects.exists()


@pytest.mark.django_db
def test_a_v1_order_whose_code_ran_out_is_still_placed_without_it(api_key, shop):
    """v1 behaviour kept: the order goes through without the code (its response does not name the code either)."""
    code = code_rule(shop, max_used=1)
    cart = ready_cart(shop)
    DiscountCode.objects.filter(pk=code.pk).update(current_used=1)

    status, _ = v1_order(api_key, shop, cart)

    assert status == 201
    assert Order.objects.get().order_body["cart"]["discounts"] == []
    assert used(code) == 1


# --- Concurrency ----------------------------------------------------------------------------------------------------


def stored_order(channel, email):
    """An order as ``Order.create`` stores it, holding the code."""
    discount = ValidatedDiscountData(
        code=CODE,
        sku=None,
        quantity=None,
        clear_discounts=False,
        item_code=None,
        price_discount=0,
        percent_discount=0,
        free_shipping=False,
        free_shipping_methods=[],
        status=ItemStatus.VALID,
        free_order=False,
        min_order_amount=0.0,
        extra_value=10,
        target="all",
        modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT,
    )
    body = {"currency_code": "EUR", "cart": {"discounts": [sanitize(asdict(discount))]}}
    return Order.objects.create(channel=channel, order_body=body, billing_email=email)


@pytest.mark.django_db(transaction=True)
def test_two_orders_racing_for_the_last_use_do_not_both_take_it(channel):
    """Both orders passed the cart check; the first holds the last use uncommitted when the second counts."""
    code = code_rule(channel, max_used=1)
    orders = [stored_order(channel, f"buyer{n}@novatrade.test") for n in (1, 2)]
    first_counted, second_counting = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def count(name, order, hold=0.0):
        try:
            with transaction.atomic():
                use_discounts(order.order_body["cart"]["discounts"], channel, order=order)
                if hold:
                    first_counted.set()
                    second_counting.wait(timeout=5)
                    time.sleep(hold)  # the second order waits on the row lock now
            outcome[name] = "counted"
        except Exception as error:  # noqa: BLE001 — reported by the assertions below
            outcome[name] = getattr(error, "reason", type(error).__name__)
        finally:
            first_counted.set()
            connection.close()

    def second():
        first_counted.wait(timeout=5)
        second_counting.set()
        count("second", orders[1])

    threads = [threading.Thread(target=count, args=("first", orders[0], 0.5)), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert outcome == {"first": "counted", "second": "usage_limit_reached"}
    assert used(code) == 1
    assert list(UsedCoupon.objects.values_list("order", flat=True)) == [orders[0].pk]
