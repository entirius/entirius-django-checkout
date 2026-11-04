# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A discount code is unique per (channel, currency).

Two code rows clash when they hold the same code (exact match, as ``validate_discounts`` reads it) on rules that share
a channel and a currency; a rule without currencies holds every currency. Active or not, typed or automatic: every
rule counts, so switching a rule on never needs a check. The same code on rules of other channels or of disjoint
currencies is fine (one code per currency, as the Emporium seed does). Write paths refuse a new clash
(``refuse_clash``, ``refuse_scope_change``); clashes already stored keep working, and the ``checkout.discount_codes``
system check lists them (``stored_clashes``).
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import combinations, groupby
from operator import itemgetter

from django.db.models import Count, QuerySet

from django_checkout.models import DiscountCode, DiscountRuleCode
from django_checkout.services.exceptions import ClientError


@dataclass(frozen=True)
class Scope:
    """Where a rule applies: channel idx and currency iso3 (no currencies = every currency)."""

    channels: frozenset[str]
    currencies: frozenset[str]

    @classmethod
    def of(cls, rule: DiscountRuleCode) -> "Scope":
        return cls(
            frozenset(channel.idx for channel in rule.channels.all()),
            frozenset(currency.iso3 for currency in rule.currencies.all()),
        )

    def overlap(self, other: "Scope") -> tuple[frozenset[str], frozenset[str]] | None:
        """Shared channels and currencies (no currencies = every currency), or None when the scopes are apart."""
        channels = self.channels & other.channels
        if not channels:
            return None
        if self.currencies and other.currencies:
            currencies = self.currencies & other.currencies
            return (channels, currencies) if currencies else None
        return channels, self.currencies or other.currencies


@dataclass(frozen=True)
class CodeClash:
    code: str
    code_id: int  # the other row
    rule_id: int  # its rule
    channels: tuple[str, ...]
    currencies: tuple[str, ...]  # () = every currency

    def describe(self) -> str:
        currencies = ", ".join(self.currencies) or "every currency"
        return (
            f"Code '{self.code}' is already used by discount rule {self.rule_id} in channel "
            f"{', '.join(self.channels)} for {currencies}; a code is unique per channel and currency."
        )


class DiscountCodeClash(ClientError, ValueError):
    """A write would make a code clash; ``field`` is the request field it belongs to."""

    def __init__(self, field: str, clash: CodeClash):
        super().__init__(clash.describe())
        self.field = field
        self.clash = clash


def find_clashes(
    codes: Iterable[str] | QuerySet,
    scope: Scope,
    *,
    exclude_code_id: int | None = None,
    exclude_rule_id: int | None = None,
) -> list[CodeClash]:
    """Rows holding one of ``codes`` on a rule whose scope overlaps ``scope``."""
    if not scope.channels:
        return []
    rows = DiscountCode.objects.filter(code__in=codes, rule__channels__idx__in=scope.channels)
    if exclude_code_id is not None:
        rows = rows.exclude(pk=exclude_code_id)
    if exclude_rule_id is not None:
        rows = rows.exclude(rule_id=exclude_rule_id)
    rows = list(rows.values_list("pk", "code", "rule_id").distinct().order_by("pk"))
    scopes = _scopes({rule_id for _, _, rule_id in rows})
    return [
        _clash(code, pk, rule_id, overlap) for pk, code, rule_id in rows if (overlap := scope.overlap(scopes[rule_id]))
    ]


def refuse_clash(code: str, rule: DiscountRuleCode, *, field: str, exclude_code_id: int | None = None) -> None:
    """Raise ``DiscountCodeClash`` when ``code`` on ``rule`` would clash (another row of the same rule counts too)."""
    if clashes := find_clashes([code], Scope.of(rule), exclude_code_id=exclude_code_id):
        raise DiscountCodeClash(field, clashes[0])


def refuse_scope_change(rule: DiscountRuleCode, before: Scope, after: Scope, *, field: str) -> None:
    """Raise when moving the rule from ``before`` to ``after`` makes one of its codes clash with a new row.

    A clash the rule already had stays allowed, so a stored clash never blocks narrowing it or an unrelated edit.
    """
    if after == before:
        return
    codes = rule.codes.values("code")
    known = {clash.code_id for clash in find_clashes(codes, before, exclude_rule_id=rule.pk)}
    if new := [clash for clash in find_clashes(codes, after, exclude_rule_id=rule.pk) if clash.code_id not in known]:
        raise DiscountCodeClash(field, new[0])


def stored_clashes() -> dict[str, list[str]]:
    """Every stored clash, by channel idx: ``"CODE: rules 1, 2 (EUR)"`` or ``"CODE: rule 1 twice (every currency)"``."""
    report = defaultdict(dict)  # a dict keeps the order and drops a pair listed twice
    for code, first, second, (channels, currencies) in _overlapping_pairs():
        for channel in sorted(channels):
            report[channel][_pair(code, first, second, currencies)] = None
    return {channel: list(pairs) for channel, pairs in report.items()}


def _overlapping_pairs() -> Iterator[tuple[str, int, int, tuple[frozenset[str], frozenset[str]]]]:
    shared = DiscountCode.objects.values("code").annotate(rows=Count("pk")).filter(rows__gt=1).values("code")
    rows = list(DiscountCode.objects.filter(code__in=shared).values_list("code", "rule_id").order_by("code", "pk"))
    scopes = _scopes({rule_id for _, rule_id in rows})
    for code, group in groupby(rows, key=itemgetter(0)):
        for first, second in combinations([rule_id for _, rule_id in group], 2):
            if overlap := scopes[first].overlap(scopes[second]):
                yield code, first, second, overlap


def _pair(code: str, first: int, second: int, currencies: frozenset[str]) -> str:
    rules = f"rule {first} twice" if first == second else f"rules {first}, {second}"
    return f"{code}: {rules} ({', '.join(sorted(currencies)) or 'every currency'})"


def _scopes(rule_ids: set[int]) -> dict[int, Scope]:
    rules = DiscountRuleCode.objects.filter(pk__in=rule_ids).prefetch_related("channels", "currencies")
    return {rule.pk: Scope.of(rule) for rule in rules}


def _clash(code: str, code_id: int, rule_id: int, overlap: tuple[frozenset[str], frozenset[str]]) -> CodeClash:
    channels, currencies = overlap
    return CodeClash(code, code_id, rule_id, tuple(sorted(channels)), tuple(sorted(currencies)))
