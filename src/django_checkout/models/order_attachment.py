# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from time import time

from django.core.files.storage import FileSystemStorage
from django.db import models

from django_checkout import settings
from django_checkout.models import Order


def order_directory_path(instance, filename):
    filename = str(int(time())) + "-" + filename
    return f"{instance.order.order_id}/{filename}"


def order_storage():
    return FileSystemStorage(location=settings.ORDER_DIR)


class OrderAttachment(models.Model):
    name = models.CharField(
        max_length=256,
        blank=True,
        null=True,
        help_text="It will automatically be filled in with the file name when saving",
    )
    order: "Order" = models.ForeignKey("Order", null=True, on_delete=models.PROTECT)
    attachment = models.FileField(blank=True, null=True, upload_to=order_directory_path, storage=order_storage)
    objects = models.Manager()

    @property
    def get_download_url(self):
        return f"/orders/{self.order.order_id}/file/{self.pk}/customer/{self.order.customer.uid}"

    def save(self, *args, **kwargs):
        self.name = self.attachment.name
        super().save(*args, **kwargs)
