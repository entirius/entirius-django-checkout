# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The legacy key path (no django_access): masked admin pages and the key generate commands.

``access_installed`` is patched to False, so these run on both gate paths.
"""

from importlib import import_module
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.core.management import call_command

from django_checkout.models import APIAdminKey, APIKey


@pytest.fixture
def legacy():
    with patch("django_checkout.admin.access_installed", return_value=False):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize("model", [APIKey, APIAdminKey])
def test_legacy_admin_masks_the_key_and_allows_edits(model, legacy, channel, admin_user, rf):
    import django_checkout.admin  # noqa: F401 — registers the key admins (no autodiscover in the test settings)

    key = model.objects.create(channel=channel)
    model_admin = admin.site.get_model_admin(model)
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request, key)
    for response in (model_admin.changelist_view(request), model_admin.change_view(request, str(key.pk))):
        html = response.render().content.decode()
        assert response.status_code == 200
        assert key.key not in html
        assert f"…{key.key[-4:]}" in html


@pytest.mark.django_db
@pytest.mark.parametrize(("command", "model"), [("generate-api-key", APIKey), ("generate-api-admin-key", APIAdminKey)])
def test_legacy_key_commands_create_a_row(command, model, channel, tmp_path):
    # By module: accounts' generate-api-admin-key shadows checkout's under the same name.
    module = import_module(f"django_checkout.management.commands.{command}")
    path = tmp_path / "key"
    with patch.object(module, "access_installed", return_value=False):
        call_command(module.Command(), channel.idx, file_path=str(path))
    row = model.objects.get(channel=channel)
    assert path.read_text() == row.key
