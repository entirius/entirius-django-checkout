# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration checks that read the database (``manage.py check --database default``, ``migrate``)."""

from django.core.checks import Tags, register
from django.core.checks import Warning as CheckWarning
from django.db import DatabaseError

from django_checkout.domain.payment_provider.payu_signature import SECOND_KEY_FIELD
from django_checkout.enums import PaymentProvider

PAYU_PROVIDERS = (PaymentProvider.PAYU, PaymentProvider.PAYU_CARD, PaymentProvider.PAYU_BLIK)


def _payu_methods_without_second_key(database: str) -> list[str]:
    from django_checkout.models import PaymentMethod

    rows = (
        PaymentMethod.objects.using(database)
        .filter(provider__in=PAYU_PROVIDERS)
        .values_list("channel__idx", "code", "additional_data")
    )
    try:
        return [f"[{idx}] {code}" for idx, code, data in rows if not (data or {}).get(SECOND_KEY_FIELD)]
    except DatabaseError:  # `migrate` runs the checks first: the tables may not exist yet
        return []


@register(Tags.database)
def payu_methods_have_second_key(app_configs, databases=None, **kwargs) -> list[CheckWarning]:
    """PayU notifications are refused (403) for a method without ``second_key``: its payments stay unconfirmed."""
    missing = sorted({name for database in databases or () for name in _payu_methods_without_second_key(database)})
    if not missing:
        return []
    return [
        CheckWarning(
            f"PayU payment methods without '{SECOND_KEY_FIELD}' in additional_data: {', '.join(missing)}. "
            "Their payment notifications are refused, so paid orders are never confirmed.",
            hint=f"Set '{SECOND_KEY_FIELD}' (the second key of the POS in the PayU merchant panel) on each method.",
            id="django_checkout.W001",
        )
    ]
