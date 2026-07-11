"""django-checkout package.

Compat shim: Django 5.1 renamed CheckConstraint(check=) -> CheckConstraint(condition=).
This module is written against the 5.1+ kwarg name. On older Django (4.2, 5.0),
translate condition= to check= so the same source works on both.
"""

from __future__ import annotations

import django
from django.db import models

if django.VERSION < (5, 1):
    _orig_init = models.CheckConstraint.__init__

    def _init(self, *args, **kwargs):
        if "condition" in kwargs and "check" not in kwargs:
            kwargs["check"] = kwargs.pop("condition")
        _orig_init(self, *args, **kwargs)

    models.CheckConstraint.__init__ = _init
