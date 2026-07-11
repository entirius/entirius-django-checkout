# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models
from django.db.models.fields import CharField
from django.db.models.fields.json import JSONField

from django_checkout.enums import OrderStatus

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


class OrderStatusLabel(models.Model):
    channel: "Channel" = models.ForeignKey("Channel", on_delete=models.CASCADE)
    status = CharField(max_length=24, choices=OrderStatus.choices)
    name_t9n = JSONField(blank=True, null=True)

    objects = models.Manager()

    def name(self):
        name = None
        try:
            if self.name_t9n is not None:
                name = self.name_t9n[self.channel.default_language.iso2]
        except KeyError:
            pass
        return name

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "status"], name="one_label_per_channel")]
