# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django_filters.filterset import FilterSetMetaclass
from django_regional.models import Country
from django_utils.api.exceptions import BadRequest

from django_checkout.settings import COUNTRY_CHECKOUT_MECHANISM

if TYPE_CHECKING:
    from django_accounts.models.customer import Customer

    from django_checkout.models.channel import Channel


def paginate(params: dict, objs_list: QuerySet) -> tuple[dict, QuerySet]:
    """
    Paginates list or queryset according to request params

    Ex:
    qs = Model.objects.all()
    pagination, paginated_qs = paginate(request.GET, qs)
    """
    which_page = int(params.get("page", 1))
    objs_per_page = int(params.get("limit", 10))
    if objs_per_page > 100:
        raise BadRequest(message="Requesting more than 100 records per request is not allowed")
    else:
        paginator = Paginator(objs_list, objs_per_page)
        pages = paginator.num_pages
        records = paginator.count
        pagination_dict = {"page": which_page, "limit": objs_per_page, "pages": pages, "records": records}
        page = paginator.page(which_page)
        return pagination_dict, page.object_list


def filter_queryset(filterset: FilterSetMetaclass, params: dict, queryset: QuerySet, request=None) -> QuerySet:
    """
    Filters queryset according to request params

    Ex:
    qs = Model.objects.all()
    filtered_qs = filter_queryset(ModelFilter, request.GET, qs)
    """
    filter_obj = filterset(params, request=request, queryset=queryset)
    return filter_obj.qs


def resolve_language(channel: "Channel", params: dict) -> str:
    lang = params.get("lang", None)
    checkout_lang = params.get("checkout_lang", None)
    if lang is None:
        if checkout_lang is None:
            return channel.default_language.iso2
        else:
            return checkout_lang
    else:
        return lang


def resolve_currency(channel: "Channel", params: dict) -> str:
    curr = params.get("currency", None)
    checkout_curr = params.get("checkout_currency", None)
    if curr is None:
        if checkout_curr is None:
            return channel.default_currency.iso3
        else:
            return checkout_curr
    else:
        return curr


class CountrySourceCheckoutMechanism:
    CART_BODY_OR_JSON_CART_OR_DEFAULT_COUNTRY_CHANNEL = 0
    HEADER_CF_OR_CART_BODY_OR_CART_JSON_OR_DEFAULT_COUNTRY_CHANNEL = 1
    HEADER_CF = 2


def resolve_country(channel: "Channel", params: dict, customer: "Customer") -> tuple[str, str | None]:
    count = params.get("country", None)
    checkout_count = params.get("checkout_country", None)
    geo_country = params.get("geo_country", None)
    if customer and getattr(customer, "last_session_country", None):
        last_session_country = customer.last_session_country.iso2
        currency = customer.last_session_country.default_currency.iso3
    else:
        geo_country = Country.objects.filter(iso2=geo_country).first() if geo_country else None
        currency = geo_country.default_currency.iso3 if geo_country else None
        last_session_country = geo_country.iso2 if geo_country else None
    channel_default_country = str(channel.default_country.iso2)

    match COUNTRY_CHECKOUT_MECHANISM:
        case CountrySourceCheckoutMechanism.CART_BODY_OR_JSON_CART_OR_DEFAULT_COUNTRY_CHANNEL:
            source_country = count or checkout_count or channel_default_country
            currency = None
        case CountrySourceCheckoutMechanism.HEADER_CF_OR_CART_BODY_OR_CART_JSON_OR_DEFAULT_COUNTRY_CHANNEL:
            source_country = last_session_country or count or checkout_count or channel_default_country
        case CountrySourceCheckoutMechanism.HEADER_CF:
            source_country = last_session_country or "--"

        case _:
            source_country = count or checkout_count or channel_default_country

    return source_country, currency


def unpack_t9n(channel: "Channel", params: dict, t9n_field: dict | None) -> str | None:
    if t9n_field is None:
        return None
    else:
        lang = resolve_language(channel, params)

        result = t9n_field.get(lang, None)

    return result


def merge_prices_dictionaries(dict1, dict2):
    # update doeant work as expected

    merged_dict = {}

    for key, value in dict1.items():
        merged_dict[key] = value

    for key, value in dict2.items():
        merged_dict[key] = value

    return merged_dict


# TODO replace with functools group_by at some point
def merge_on_match(left_key: str, right_key: str, key_to_add: str, left: list[dict], right: list[dict]) -> None:
    for obj in left:
        if key_to_add not in obj.keys():
            obj[key_to_add] = []
        match_idx = None
        for idx, to_add in enumerate(right):
            if obj[left_key] == to_add[right_key] or (
                isinstance(to_add[right_key], set) and obj[left_key] in to_add[right_key]
            ):
                match_idx = idx
                break
            else:
                pass
        if match_idx is not None:
            while match_idx < len(right) and (
                obj[left_key] == right[match_idx][right_key]
                or (isinstance(right[match_idx][right_key], set) and obj[left_key] in right[match_idx][right_key])
            ):
                obj[key_to_add].append(right[match_idx])
                match_idx += 1
        else:
            pass

    # Clean match keys on the right side
    for obj in left:
        for elem in obj[key_to_add]:
            elem.pop(right_key, None)
