# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django.db import models


class ShippingPriceByWeightQuerySet(models.QuerySet):
    def get_price_by_weight_from_matrix(self, weight) -> Decimal:

        for matrix_weight in self.order_by("-weight"):
            if not weight:
                weight = 0
            if weight >= matrix_weight.weight:
                return matrix_weight.price_brutto


class ShippingPriceMatrixManager(models.Manager):
    def get_all_feature_attr_idx_for_shipping_option(self):
        return self.objects.filter(filter_type=self.FilterType.ATTR).valus_list("value", "idx")


class ShippingPriceByWeightManager(models.Manager):
    def get_queryset(self):
        return ShippingPriceByWeightQuerySet(self.model, using=self._db)
