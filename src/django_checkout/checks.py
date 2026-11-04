# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration checks that read the database (``manage.py check --database default``, ``migrate``).

``checkout.discount_codes`` carries the ``entirius_config`` tag and its code instead of ``database``, so django-munin
shows it in the CMS; it reads the database only when the run passes one and the table exists.
"""

from django.core.checks import Tags, register
from django.core.checks import Warning as CheckWarning
from django.db import DatabaseError, connections

from django_checkout.domain.payment_provider.paypal_webhook import WEBHOOK_ID_FIELD
from django_checkout.domain.payment_provider.payu_signature import SECOND_KEY_FIELD
from django_checkout.enums import PaymentProvider

PAYU_PROVIDERS = (PaymentProvider.PAYU, PaymentProvider.PAYU_CARD, PaymentProvider.PAYU_BLIK)
CODE_CLASHES = "checkout.discount_codes"
CODE_CLASHES_FIX_URL = "https://github.com/entirius/entirius-django-checkout/blob/master/docs/api/discounts.md#one-code-per-channel-and-currency"


class _Subject(dict):
    """Row metadata munin reads as a dict; `manage.py check` prints `str(obj)` as the label, so name the subject."""

    def __str__(self) -> str:
        return self.get("scope") or "discount codes"


def _methods_without(database: str, providers: tuple[str, ...], field: str) -> list[str]:
    from django_checkout.models import PaymentMethod

    rows = (
        PaymentMethod.objects.using(database)
        .filter(provider__in=providers)
        .values_list("channel__idx", "code", "additional_data")
    )
    try:
        return [f"[{idx}] {code}" for idx, code, data in rows if not (data or {}).get(field)]
    except DatabaseError:  # `migrate` runs the checks first: the tables may not exist yet
        return []


def _missing(databases, providers: tuple[str, ...], field: str) -> list[str]:
    return sorted({name for database in databases or () for name in _methods_without(database, providers, field)})


@register(Tags.database)
def payu_methods_have_second_key(app_configs, databases=None, **kwargs) -> list[CheckWarning]:
    """PayU notifications are refused (403) for a method without ``second_key``: its payments stay unconfirmed."""
    missing = _missing(databases, PAYU_PROVIDERS, SECOND_KEY_FIELD)
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


@register(Tags.database)
def paypal_methods_have_webhook_id(app_configs, databases=None, **kwargs) -> list[CheckWarning]:
    """PayPal notifications are refused (403) for a method without ``webhook_id``: only the buyer's return confirms."""
    missing = _missing(databases, (PaymentProvider.PAYPAL,), WEBHOOK_ID_FIELD)
    if not missing:
        return []
    return [
        CheckWarning(
            f"PayPal payment methods without '{WEBHOOK_ID_FIELD}' in additional_data: {', '.join(missing)}. "
            "Their webhook notifications are refused, so an order is confirmed only when the buyer returns "
            "to the shop.",
            hint=f"Set '{WEBHOOK_ID_FIELD}' (PayPal developer dashboard: the app's Webhooks, Webhook ID) on each method.",
            id="django_checkout.W002",
        )
    ]


@register("entirius_config", CODE_CLASHES)
def discount_codes_unique(app_configs, databases=None, **kwargs) -> list[CheckWarning]:
    """A code on two rules that share a channel and a currency: the cart and the usage count may pick different rows."""
    if not _discount_tables_ready(databases):
        return []
    from django_checkout.services.code_clashes import stored_clashes

    return [_clash_warning(channel, pairs) for channel, pairs in sorted(stored_clashes().items())]


def _discount_tables_ready(databases) -> bool:
    from django_checkout.models import DiscountCode

    return (
        "default" in (databases or ())
        and DiscountCode._meta.db_table in connections["default"].introspection.table_names()
    )


def _clash_warning(channel: str, pairs: list[str]) -> CheckWarning:
    return CheckWarning(
        "Discount codes shared by rules of one channel and currency",
        hint=(
            f"{'; '.join(pairs)}. A cart applies one of the rules and an order may count the use on the other, so "
            "usage limits drift. Give each rule its own code, or split the rules' currencies; the admin refuses new "
            "clashes, these were stored before."
        ),
        id=CODE_CLASHES,
        obj=_Subject(scope=channel, state="unconfigured", severity="medium", fix_url=CODE_CLASHES_FIX_URL),
    )
