# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""PayU notifications are refused without ``second_key``; the check names the methods that lack it."""

import pytest
from django.core.checks import Warning as CheckWarning
from django.core.checks import run_checks
from django.db import ProgrammingError

from django_checkout.enums import PaymentProvider
from django_checkout.models import PaymentMethod
from django_checkout.models.payment_method import PaymentMethodQuerySet

CHECK_ID = "django_checkout.W001"
CONFIGURED = {"pos_id": "1", "client_id": "1", "second_key": "synthetic-second-key"}


def _payu_warnings(databases=("default",)) -> list:
    return [message for message in run_checks(databases=databases) if message.id == CHECK_ID]


def _method(channel, code: str, provider: str, additional_data: dict | None) -> PaymentMethod:
    return PaymentMethod.objects.create(
        channel=channel, code=code, provider=provider, name_t9n={"en": code}, additional_data=additional_data
    )


@pytest.mark.django_db
@pytest.mark.parametrize("provider", [PaymentProvider.PAYU, PaymentProvider.PAYU_CARD, PaymentProvider.PAYU_BLIK])
@pytest.mark.parametrize("additional_data", [None, {}, {"pos_id": "1"}, {"second_key": ""}])
def test_payu_method_without_second_key_is_reported(channel, provider, additional_data):
    _method(channel, "pay", provider, additional_data)

    (warning,) = _payu_warnings()

    assert isinstance(warning, CheckWarning)
    assert "second_key" in warning.msg
    assert "[test-channel] pay" in warning.msg


@pytest.mark.django_db
def test_every_method_without_the_key_is_listed_in_one_message(channel, other_channel):
    _method(channel, "payu", PaymentProvider.PAYU, {})
    _method(other_channel, "blik", PaymentProvider.PAYU_BLIK, {})

    (warning,) = _payu_warnings()

    assert "[test-channel] payu" in warning.msg
    assert "[other-channel] blik" in warning.msg


@pytest.mark.django_db
def test_configured_and_non_payu_methods_are_not_reported(channel):
    _method(channel, "payu", PaymentProvider.PAYU, CONFIGURED)
    _method(channel, "transfer", PaymentProvider.TRANSFER, {})

    assert _payu_warnings() == []


@pytest.mark.django_db
def test_the_message_never_carries_configuration_values(channel):
    _method(channel, "payu", PaymentProvider.PAYU, {"client_secret": "synthetic-client-value"})

    (warning,) = _payu_warnings()

    assert "synthetic-client-value" not in f"{warning.msg} {warning.hint}"


def test_a_database_without_the_tables_reports_nothing(monkeypatch):
    """``migrate`` runs the checks before it creates or alters the tables."""

    def no_table(self):
        raise ProgrammingError("relation does not exist")

    monkeypatch.setattr(PaymentMethodQuerySet, "__iter__", no_table)

    assert _payu_warnings() == []
