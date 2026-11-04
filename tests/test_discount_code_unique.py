# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A discount code is unique per (channel, currency).

Two rules clash on a code when both hold it, share a channel and share a currency; a rule without currencies holds
every currency. Every write path refuses a new clash; clashes already stored keep working and the
``checkout.discount_codes`` check lists them.
"""

import pytest
from django.contrib import admin
from django.core.checks import Tags, run_checks
from django.db import connections
from django.forms import inlineformset_factory
from django_regional.models import Currency
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.views.admin.discount_views import (
    AdminDiscountCodeDetailView,
    AdminDiscountCodeListView,
    AdminDiscountRuleDetailView,
    AdminDiscountRuleListView,
)
from django_checkout.models import DiscountCode, DiscountRuleCode

factory = APIRequestFactory()
CHECK_CODE = "checkout.discount_codes"


@pytest.fixture(autouse=True)
def v2_errors(settings):
    """The v2 error envelope, as the service wires it (the module's test settings keep DRF's default handler)."""
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
    }


@pytest.fixture(autouse=True)
def currencies(db, default_currency):
    for iso3 in ("PLN", "USD"):
        Currency.objects.get_or_create(iso3=iso3, defaults={"name_en": iso3, "name_pl": iso3})


def _rule(*channels, currencies=(), name="rule"):
    rule = DiscountRuleCode.objects.create(name=name, modifier="percent_discount", extra_value=10)
    rule.channels.set(channels)
    rule.currencies.set(Currency.objects.filter(iso3__in=currencies))
    return rule


def _code(rule, code="SUMMER"):
    return DiscountCode.objects.create(rule=rule, code=code)


def _call(view, method, user, data=None, **kwargs):
    request = getattr(factory, method)("/", data, format="json")
    force_authenticate(request, user=user)
    return view.as_view()(request, **kwargs)


def _post_code(channel, rule, user, code="SUMMER"):
    return _call(AdminDiscountCodeListView, "post", user, {"code": code}, channel_idx=channel.idx, rule_id=rule.pk)


def _refusal(response) -> dict:
    assert response.status_code == 400, response.data
    assert response.data["error"] == "VALIDATION_ERROR"
    (detail,) = response.data["details"]
    assert detail["issue"] == "UNIQUE"
    return detail


# --- v2 admin API: codes ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("held", "new", "shared"),
    [
        (["EUR"], ["EUR"], "EUR"),
        ([], ["EUR"], "EUR"),
        (["EUR"], [], "EUR"),
        ([], [], "every currency"),
        (["EUR", "PLN"], ["EUR"], "EUR"),
    ],
)
def test_a_code_another_rule_holds_in_the_channel_and_currency_is_refused(channel, admin_user, held, new, shared):
    holder = _rule(channel, currencies=held)
    _code(holder)
    rule = _rule(channel, currencies=new)

    detail = _refusal(_post_code(channel, rule, admin_user))

    assert detail["field"] == "code"
    assert "'SUMMER'" in detail["description"]
    assert f"discount rule {holder.pk}" in detail["description"]
    assert f"for {shared}" in detail["description"]
    assert not rule.codes.exists()


@pytest.mark.django_db
def test_the_same_code_in_another_currency_or_channel_is_accepted(channel, other_channel, admin_user):
    _code(_rule(channel, currencies=["PLN"]))
    _code(_rule(other_channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])

    assert _post_code(channel, rule, admin_user).status_code == 201


@pytest.mark.django_db
def test_a_code_twice_on_one_rule_is_refused(channel, admin_user):
    rule = _rule(channel, currencies=["EUR"])
    _code(rule)

    detail = _refusal(_post_code(channel, rule, admin_user))

    assert detail["field"] == "code"
    assert rule.codes.count() == 1


@pytest.mark.django_db
def test_renaming_a_code_into_a_clash_is_refused(channel, admin_user):
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])
    code = _code(rule, "WINTER")

    response = _call(
        AdminDiscountCodeDetailView,
        "patch",
        admin_user,
        {"code": "SUMMER"},
        channel_idx=channel.idx,
        rule_id=rule.pk,
        code_id=code.pk,
    )

    assert _refusal(response)["field"] == "code"
    code.refresh_from_db()
    assert code.code == "WINTER"


@pytest.mark.django_db
def test_a_stored_clash_keeps_working(channel, admin_user):
    """The CMS sends the whole code and the whole rule on every save: an unchanged clash must not block an edit."""
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])
    code = _code(rule)

    code_edit = _call(
        AdminDiscountCodeDetailView,
        "patch",
        admin_user,
        {"code": "SUMMER", "max_used": 10},
        channel_idx=channel.idx,
        rule_id=rule.pk,
        code_id=code.pk,
    )
    rule_edit = _call(
        AdminDiscountRuleDetailView,
        "patch",
        admin_user,
        {"name": "Renamed", "channels": [channel.idx], "currencies": ["EUR"]},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )

    assert code_edit.status_code == 200
    assert code_edit.data["max_used"] == 10
    assert rule_edit.status_code == 200
    assert rule_edit.data["name"] == "Renamed"


# --- v2 admin API: rules ---


@pytest.mark.django_db
def test_a_rule_created_with_a_clashing_code_is_refused_whole(channel, admin_user):
    _code(_rule(channel, currencies=["EUR"]))
    rules_before = DiscountRuleCode.objects.count()
    body = {"modifier": "percent_discount", "extra_value": 10, "currencies": ["EUR"], "codes": [{"code": "SUMMER"}]}

    response = _call(AdminDiscountRuleListView, "post", admin_user, body, channel_idx=channel.idx)

    assert _refusal(response)["field"] == "codes.0.code"
    assert DiscountRuleCode.objects.count() == rules_before


@pytest.mark.django_db
def test_a_new_rule_with_one_code_twice_is_refused(channel, admin_user):
    body = {"modifier": "percent_discount", "extra_value": 10, "codes": [{"code": "A"}, {"code": "B"}, {"code": "A"}]}

    response = _call(AdminDiscountRuleListView, "post", admin_user, body, channel_idx=channel.idx)

    assert _refusal(response)["field"] == "codes.2.code"
    assert not DiscountCode.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("widened", [["PLN", "EUR"], []])
def test_widening_the_currencies_into_a_clash_is_refused(channel, admin_user, widened):
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["PLN"])
    _code(rule)

    response = _call(
        AdminDiscountRuleDetailView,
        "patch",
        admin_user,
        {"currencies": widened},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )

    assert _refusal(response)["field"] == "currencies"
    assert list(rule.currencies.values_list("iso3", flat=True)) == ["PLN"]


@pytest.mark.django_db
def test_adding_a_channel_into_a_clash_is_refused(channel, other_channel, admin_user):
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(other_channel, currencies=["EUR"])
    _code(rule)

    response = _call(
        AdminDiscountRuleDetailView,
        "patch",
        admin_user,
        {"channels": [channel.idx]},
        channel_idx=other_channel.idx,
        rule_id=rule.pk,
    )

    assert _refusal(response)["field"] == "channels"
    assert list(rule.channels.values_list("idx", flat=True)) == [other_channel.idx]


@pytest.mark.django_db
@pytest.mark.parametrize("currencies", [["PLN"], ["EUR", "PLN", "USD"]])
def test_a_scope_change_that_adds_no_clash_is_accepted(channel, admin_user, currencies):
    """Narrowing a stored clash away, or widening without a new clash, goes through."""
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR", "PLN"])
    _code(rule)

    response = _call(
        AdminDiscountRuleDetailView,
        "patch",
        admin_user,
        {"currencies": currencies},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )

    assert response.status_code == 200
    assert sorted(response.data["currencies"]) == sorted(currencies)


# --- Django admin ---


def _admin_form(request, model, data, instance=None):
    import django_checkout.admin  # noqa: F401 — registers the admins (no autodiscover in the test settings)

    form_class = admin.site.get_model_admin(model).get_form(request, instance)
    return form_class(data=data, instance=instance)


@pytest.fixture
def admin_request(rf, admin_user):
    request = rf.get("/")
    request.user = admin_user
    return request


@pytest.mark.django_db
def test_the_code_admin_refuses_a_clash(channel, admin_request):
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])

    form = _admin_form(
        admin_request, DiscountCode, {"rule": rule.pk, "code": "SUMMER", "max_used": 1, "current_used": 0}
    )

    assert not form.is_valid()
    assert "SUMMER" in form.errors["code"][0]


@pytest.mark.django_db
def test_the_code_admin_saves_a_stored_clash_unchanged(channel, admin_request):
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])
    code = _code(rule)

    form = _admin_form(
        admin_request, DiscountCode, {"rule": rule.pk, "code": "SUMMER", "max_used": 5, "current_used": 0}, code
    )

    assert form.is_valid(), form.errors


def _codes_formset(rule, rows, *, scope=None):
    import django_checkout.admin as checkout_admin

    if scope is not None:
        rule.pending_scope = scope
    formset_class = inlineformset_factory(
        DiscountRuleCode, DiscountCode, formset=checkout_admin.DiscountCodeInlineFormSet, fields=["code"], extra=0
    )
    data = {"codes-TOTAL_FORMS": str(len(rows)), "codes-INITIAL_FORMS": "0"}
    data |= {f"codes-{index}-code": code for index, code in enumerate(rows)}
    return formset_class(data=data, instance=rule, prefix="codes")


@pytest.mark.django_db
def test_the_rule_admin_refuses_a_clashing_inline_code(channel):
    from django_checkout.services.code_clashes import Scope

    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["EUR"])

    formset = _codes_formset(rule, ["SUMMER"], scope=Scope.of(rule))

    assert not formset.is_valid()
    assert "SUMMER" in " ".join(formset.non_form_errors())


@pytest.mark.django_db
def test_the_rule_admin_refuses_one_code_twice(channel):
    from django_checkout.services.code_clashes import Scope

    rule = _rule(channel, currencies=["EUR"])

    formset = _codes_formset(rule, ["A", "A"], scope=Scope.of(rule))

    assert not formset.is_valid()
    assert formset.non_form_errors() == ["Code 'A' appears twice on this rule."]


@pytest.mark.django_db
def test_the_rule_admin_refuses_widening_into_a_clash(channel):
    """The scope the rule form is about to save, not the stored one, decides."""
    from django_checkout.services.code_clashes import Scope

    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["PLN"])
    _code(rule)

    formset = _codes_formset(rule, [], scope=Scope(frozenset({channel.idx}), frozenset({"PLN", "EUR"})))

    assert not formset.is_valid()
    assert "SUMMER" in " ".join(formset.non_form_errors())


# --- system check ---


def _clash_warnings(databases=("default",)) -> list:
    return [m for m in run_checks(tags=[CHECK_CODE], databases=databases) if m.id == CHECK_CODE]


@pytest.mark.django_db
def test_the_check_lists_stored_clashes_per_channel(channel, other_channel):
    first = _rule(channel, other_channel, currencies=["EUR"])
    second = _rule(channel, other_channel, currencies=["EUR", "PLN"])
    _code(first)
    _code(second)
    lonely = _rule(channel)
    _code(lonely, "TWICE")
    _code(lonely, "TWICE")

    warnings = _clash_warnings()

    assert sorted(w.obj["scope"] for w in warnings) == sorted([channel.idx, other_channel.idx])
    by_channel = {w.obj["scope"]: w for w in warnings}
    assert f"SUMMER: rules {first.pk}, {second.pk} (EUR)" in by_channel[channel.idx].hint
    assert f"TWICE: rule {lonely.pk} twice (every currency)" in by_channel[channel.idx].hint
    assert "TWICE" not in by_channel[other_channel.idx].hint
    assert by_channel[channel.idx].obj["state"] == "unconfigured"
    assert by_channel[channel.idx].obj["fix_url"]


@pytest.mark.django_db
def test_the_check_passes_codes_per_currency_and_per_channel(channel, other_channel):
    _code(_rule(channel, currencies=["EUR"]))
    _code(_rule(channel, currencies=["PLN"]))
    _code(_rule(other_channel, currencies=["EUR"]))

    assert _clash_warnings() == []


@pytest.mark.django_db
def test_the_check_is_a_configuration_health_check(channel):
    """Tag ``entirius_config`` (munin) plus its code; not ``database``, so it never runs without a database."""
    _code(_rule(channel))
    _code(_rule(channel))

    config = run_checks(tags=["entirius_config"], databases=["default"])
    database = run_checks(tags=[Tags.database], databases=["default"])

    assert any(m.id == CHECK_CODE for m in config)
    assert not any(m.id == CHECK_CODE for m in database)


@pytest.mark.django_db
def test_the_check_reads_no_database_at_boot(channel):
    _code(_rule(channel))
    _code(_rule(channel))

    assert _clash_warnings(databases=None) == []


@pytest.mark.django_db
def test_the_check_reports_nothing_before_migrate(channel, monkeypatch):
    """``migrate`` runs the database checks before it creates the tables."""
    _code(_rule(channel))
    _code(_rule(channel))
    monkeypatch.setattr(connections["default"].introspection, "table_names", lambda *args, **kwargs: [])

    assert _clash_warnings() == []


def _rule_change_post(rule, channel, currencies: list[str], codes: list[str]) -> dict:
    """The rule admin's change form as a browser posts it: the rule fields and every inline's management form."""
    data = {
        "channels": [channel.pk],
        "name": rule.name,
        "is_active": "on",
        "currencies": list(Currency.objects.filter(iso3__in=currencies).values_list("pk", flat=True)),
        "target": rule.target,
        "extra_value": "10",
        "extension": "{}",
        "modifier": rule.modifier,
        "min_order_amount": "0",
        "show_when_invalid": "on",
        "created_at_0": "2026-01-01",
        "created_at_1": "00:00:00",
    }
    for prefix in ("discount_mode_of_actions_rule", "threshold_filters", "discount_customer_mode_of_actions_rule"):
        data |= {f"{prefix}-TOTAL_FORMS": "0", f"{prefix}-INITIAL_FORMS": "0"}
    data |= {"codes-TOTAL_FORMS": str(len(codes)), "codes-INITIAL_FORMS": "0"}
    for index, code in enumerate(codes):
        data |= {f"codes-{index}-code": code, f"codes-{index}-max_used": "1", f"codes-{index}-current_used": "0"}
    return data


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("currencies", "codes"),
    [(["PLN", "EUR"], []), (["EUR"], ["SUMMER"])],
    ids=["widened-rule-with-a-stored-code", "moved-rule-with-a-new-code"],
)
def test_the_rule_admin_change_view_refuses_a_clash(rf, settings, admin_user, channel, currencies, codes):
    """Through the real change view: the scope of the posted rule form reaches the codes inline."""
    from django.contrib.messages.storage.cookie import CookieStorage

    import django_checkout.admin  # noqa: F401 — registers the admins (no autodiscover in the test settings)

    settings.ROOT_URLCONF = "tests.admin_urls"
    _code(_rule(channel, currencies=["EUR"]))
    rule = _rule(channel, currencies=["PLN"])
    if not codes:
        _code(rule)
    request = rf.post("/", _rule_change_post(rule, channel, currencies, codes))
    request.user = admin_user
    request._dont_enforce_csrf_checks = True
    request._messages = CookieStorage(request)

    response = admin.site.get_model_admin(DiscountRuleCode).change_view(request, str(rule.pk))

    assert response.status_code == 200, response.get("Location")  # a redirect would mean it saved
    (codes_inline,) = [i.formset for i in response.context_data["inline_admin_formsets"] if i.formset.prefix == "codes"]
    assert "already used by discount rule" in " ".join(codes_inline.non_form_errors())
    assert list(rule.currencies.values_list("iso3", flat=True)) == ["PLN"]
    assert rule.codes.count() == (0 if codes else 1)
