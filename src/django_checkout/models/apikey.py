# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from django.db import models
from django.utils.crypto import get_random_string

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


class APIKey(models.Model):
    channel: "Channel" = models.ForeignKey("Channel", null=True, on_delete=models.CASCADE)
    key = models.CharField(max_length=128, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = models.Manager()

    def generate_key(self):
        seed = "".join([get_random_string(length=16), str(datetime.now()), get_random_string(length=16)])
        key = sha256(seed.encode("utf-8")).hexdigest()
        return key

    def save(self, *args, **kwargs) -> None:
        if self.key is None or self.key == "":
            self.key = self.generate_key()

        return super().save(*args, **kwargs)
