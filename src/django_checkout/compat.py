# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Django version compatibility shims.

Supports Django 4.2 ↔ 6.0 in a single codebase. The breaking change between
those versions is `CheckConstraint`:
  - Django 4.x  → `CheckConstraint(check=Q(...))`
  - Django 5.1+ → `CheckConstraint(condition=Q(...))` (`check=` removed in 6.0)

Use this helper in models AND migrations to keep both versions working.
"""

import django
from django.db import models

_DJANGO_NEW_CONSTRAINT_API = django.VERSION >= (5, 1)


def check_constraint(*, name: str, condition: models.Q) -> models.CheckConstraint:
    """Cross-version `CheckConstraint`. Always pass `condition=` and `name=`."""
    if _DJANGO_NEW_CONSTRAINT_API:
        return models.CheckConstraint(condition=condition, name=name)
    return models.CheckConstraint(check=condition, name=name)
