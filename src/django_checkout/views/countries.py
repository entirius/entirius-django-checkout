# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import asdict

from django_utils.api.decorators import api_view, parse_parameters, require_http_method
from django_utils.api.exceptions import NotFound
from django_utils.api.responses import Response

from django_checkout.domain.common import BaseQueryParams
from django_checkout.domain.countries import Countries as CountriesDTO
from django_checkout.domain.countries import Country as CountryDTO
from django_checkout.models import Channel


def get_requested_or_default(requested: str | None, default: str, available: list[str]) -> str:
    """This function is used to decide which language, currency, region, etc. should be used when computing the
    response"""
    if requested is None or requested not in available:
        return default
    else:
        return requested


def _build_countries_response(channel: "Channel", language: str | None = None) -> dict:
    """Build the countries payload for a channel.

    Pure, request-independent: resolves the language against the passed
    ``channel`` object directly (no re-query by idx), so both the v1 function
    view and the v2 DRF view can share it.
    """
    available = [elem.iso2.lower() for elem in channel.languages.all()]
    default = channel.default_language.iso2
    if language is not None:
        language = language.lower()
    language = get_requested_or_default(language, default, available)
    name = "name_en" if language == "en" else "name_pl"

    countries = [*[elem for elem in channel.countries.all()]]
    if channel.default_country not in countries:
        countries.append(channel.default_country)

    result = CountriesDTO(
        countries=[CountryDTO(code=count.iso2, label=getattr(count, name), prefix=count.prefix) for count in countries],
        default_country=CountryDTO(
            code=channel.default_country.iso2,
            label=getattr(channel.default_country, name),
            prefix=channel.default_country.prefix,
        ),
    )
    return asdict(result)


@api_view
@require_http_method("GET")
@parse_parameters(BaseQueryParams.Schema())
def listing_view(request, params: BaseQueryParams, channel_idx=None, *args, **kwargs) -> "Response":
    channel = Channel.objects.filter(idx=channel_idx).first()
    if channel is None:
        raise NotFound(f"Channel {channel_idx} does not exist")
    return Response(_build_countries_response(channel, params.language))
