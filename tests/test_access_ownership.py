# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The module owns its access rules: areas and token scopes on its AppConfig, an `access_area` on every admin view."""

import os

import pytest

pytest.importorskip("django_access")
if os.environ.get("ENTIRIUS_TEST_NO_ACCESS"):  # make test-legacy: django_access is importable but not installed
    pytest.skip("access path only", allow_module_level=True)

from django.apps import apps  # noqa: E402
from django_access.catalogue.areas import DEFAULT_AREAS, Area  # noqa: E402
from django_access.catalogue.scopes import DEFAULT_SCOPES, TokenScope  # noqa: E402
from django_access.testing import assert_routes_covered  # noqa: E402

LABEL = "django_checkout"


def test_admin_routes_are_covered_by_own_declarations():
    assert_routes_covered(LABEL, urlconf="django_checkout.urls", require_own=True)


def test_declarations_match_the_access_defaults():
    """Same areas and scopes as the access defaults; delete with those defaults in a later access release."""
    config = apps.get_app_config(LABEL)
    areas = [Area(**{**item, "module": LABEL}) for item in config.access_areas]
    scopes = [TokenScope(**{**item, "module": LABEL}) for item in config.access_token_scopes]
    assert areas == [item for item in DEFAULT_AREAS if item.module == LABEL]
    assert scopes == [item for item in DEFAULT_SCOPES if item.module == LABEL]
