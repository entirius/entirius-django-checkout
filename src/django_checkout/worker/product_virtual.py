# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


def check_all_virtual_products(sku_list, channel, currency):
    try:
        from django_pim.models import KindOfProductEnum, Product
        from django_regional.models import Currency
    except ImportError:
        return False

    products = Product.objects.filter(real_product__sku__in=sku_list, shop__idx=channel.idx)
    products_count = products.count()
    virtual_products_count = products.filter(real_product__kind_of_product=KindOfProductEnum.ProductVirtual).count()

    if not virtual_products_count and not products_count:
        return False

    if virtual_products_count == products_count:
        return True
    else:
        return False
