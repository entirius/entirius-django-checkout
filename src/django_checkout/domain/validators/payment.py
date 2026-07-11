# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from typing import TYPE_CHECKING

from django_utils.api.responses import ErrorInfo

from django_checkout.domain.dto.address import Address, SimplifiedAddress
from django_checkout.domain.dto.payment import PaymentData, ValidatedPaymentData
from django_checkout.enums import ItemStatus, PaymentProvider
from django_checkout.models import PaymentMethod

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


PAY_CODE_MASK_SUFFIX = "********"


def _mask_voucher_pay_code(raw: str) -> str:
    """Display-only mask: first 4 chars of each code + fixed asterisks; PINs dropped.

    "26WTY3QY2WGN:1234,9F565XGKB9XA" -> "26WT********,9F56********".
    Idempotent (masking a masked value yields itself), so the persisted body can
    safely re-enter validation. The mask is NOT a code: attach skips entries
    containing '*' and the presence/format checks treat them as redacted.
    """
    masked_parts = []
    for part in raw.split(","):
        code = part.split(":", 1)[0].strip()
        if not code:
            continue
        if "*" in code:
            masked_parts.append(code)
        else:
            masked_parts.append(f"{code[:4]}{PAY_CODE_MASK_SUFFIX}")
    return ",".join(masked_parts)


def _is_redacted_pay_code(value) -> bool:
    """None or a masked display value — never usable as an actual code."""
    return value is None or "*" in value


def _has_attached_codes(selected: PaymentMethod, cart_record) -> bool:
    """True when the provider already holds applied codes for this cart
    (e.g. CartVoucher rows). Lets a code-requiring method stay valid on
    re-validation even though pay_code is never persisted (see SECURITY
    note below) — the attached state, not the echoed secret, is the proof."""
    if cart_record is None:
        return False
    extension = selected.get_provider().get_payment_method_extension(cart_record)
    return bool(extension.get("applied_count"))


def validate_payment_method(
    payment_method: PaymentData | list[PaymentData] | None,
    address: Address | SimplifiedAddress | None,
    channel: "Channel",
    country,
    lang,
    currency,
    shipping_option,
    item_data_list,
    checkout_payment_method: PaymentData | list[PaymentData] | None = None,
    customer=None,
    cart_record=None,
) -> tuple[list[ValidatedPaymentData] | None, list[str], list[PaymentMethod] | None]:
    """Validate one or many payment methods.

    Accepts list[PaymentData] or single PaymentData (legacy). Always returns
    (validated_list, messages_list, payment_method_models_list) — even for
    single-method input. Callers must always index [0] for legacy compat.
    """
    # Backward-compat: accept singular or None as input
    if payment_method is not None and not isinstance(payment_method, list):
        payment_method = [payment_method]
    if checkout_payment_method is not None and not isinstance(checkout_payment_method, list):
        checkout_payment_method = [checkout_payment_method]

    def build_validated_payment_data(payment: PaymentData):
        if address is None:
            return None, "You need billing address to add payment. ", None

        if country is None and payment.country_code is None:
            return None, "You need country code inside billing address or in request to add payment.", None

        address_country = address.country_code.upper() if getattr(address, "country_code", None) else None or country

        selected = PaymentMethod.objects.filter(
            channel=channel, code=payment.code, countries__iso2=address_country
        ).first()
        msg = ""
        msgs = []
        if selected:
            countries_allowed = selected.countries.all().values_list("iso2", flat=True)
            currencies_allowed = selected.currencies.all().values_list("iso3", flat=True)
            if address_country not in countries_allowed:
                msg = "Payment method not allowed for this country."
                selected = None

            elif currency.upper() not in currencies_allowed:
                msg = "Payment method not allowed for this currency."
                selected = None
        else:
            msg = "Payment method with this code does not exists."

        if selected:
            cash_on_delivery_available = shipping_option is not None and shipping_option.cash_on_delivery_available
            selected = (
                PaymentMethod.objects.filter(channel=channel, code=payment.code, countries__iso2=address_country)
                .filter_by_cash_on_delivery(cash_on_delivery_available, shipping_option)
                .get_only_pm_available_for_cart_products(item_data_list, channel, True if customer else False)
                .get_only_not_limited_pm([item.sku for item in item_data_list], channel, True if customer else False)
                .get_only_by_customer_group(customer.group if customer else None)
                .first()
            )
            if not selected:
                msg = "Payment method not available for this cart."

        if (
            selected
            and selected.provider in PaymentProvider.requires_code()
            and _is_redacted_pay_code(payment.pay_code)
            and not _has_attached_codes(selected, cart_record)
        ):
            msgs.append(
                ErrorInfo(
                    message="Pay code is required for this payment method.",
                    code="pay_code_required",
                    affected_field="cart.payment_method.pay_code",
                )
            )
            selected = None

        if (
            selected
            and not _is_redacted_pay_code(payment.pay_code)
            and PaymentProvider.validate_code(payment.pay_code, selected.provider) is False
        ):
            msgs.append(
                ErrorInfo(
                    message="Pay code is invalid for this payment method.",
                    code="pay_code_invalid",
                    affected_field="cart.payment_method.pay_code",
                )
            )
            selected = None

        if selected is not None:
            language = lang.lower() if lang is not None else ""
            if selected.provider == PaymentProvider.VOUCHER and payment.pay_code:
                redacted_pay_code = _mask_voucher_pay_code(payment.pay_code) or None
            elif selected.provider == PaymentProvider.VOUCHER:
                redacted_pay_code = None
            else:
                redacted_pay_code = payment.pay_code
            return (
                ValidatedPaymentData(
                    code=selected.code,
                    name=selected.name_t9n.get(language, payment.code),
                    bank_id=payment.bank_id,
                    card=payment.card,
                    country_code=address_country,
                    authorization_token=payment.authorization_token,
                    is_cash_on_delivery=selected.is_cash_on_delivery,
                    save_card=payment.save_card,
                    continue_url=payment.continue_url,
                    status=ItemStatus.VALID,
                    pay_code=redacted_pay_code,
                ),
                [*msgs],
                selected,
            )
        else:
            return (
                ValidatedPaymentData(
                    code=payment.code,
                    name=payment.code,
                    bank_id=payment.bank_id,
                    card=payment.card,
                    save_card=payment.save_card,
                    country_code=payment.country_code,
                    authorization_token=payment.authorization_token,
                    is_cash_on_delivery=False,
                    continue_url=payment.continue_url,
                    status=ItemStatus.INVALID,
                    pay_code=None,
                ),
                [f"Added dummy payment method. {msg}", *msgs],
                None,
            )

    # Choose source: explicit payment_method takes precedence; fall back to checkout_payment_method
    source_list = payment_method if payment_method else checkout_payment_method
    if not source_list:
        return None, [], None

    validated_list: list = []
    messages: list = []
    pm_models: list = []
    for pm_input in source_list:
        validated, msg, pm_model = build_validated_payment_data(pm_input)
        if validated is not None:
            validated_list.append(validated)
        if msg:
            if isinstance(msg, list):
                messages.extend(msg)
            else:
                messages.append(msg)
        if pm_model is not None:
            pm_models.append(pm_model)

    return validated_list, messages, pm_models
