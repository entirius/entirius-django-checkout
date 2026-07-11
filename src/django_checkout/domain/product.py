# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django.db.models import F, Q
from django_pim.models import Feature, FeatureType, FeatureTypeEnum, FrontendInputType, Product, ProductAttribute

from django_checkout.domain.dto.item import SkuQuantityData


def fetch_attributes(ids, lang: str, additional_query: list[Q] = None):
    """Used only in deprecated function -> DEPRECATED"""

    if additional_query is None:
        additional_query = []
    result = list(
        ProductAttribute.objects.filter(*additional_query, product__pk__in=ids)
        .values(
            "value_txt",
            f"value_txt_t9n__{lang}",
            "value_bool",
            "value_json",
            "value_decimal",
            value_select=F("attribute__idx"),
            value_select_name=F(f"attribute__name_t9n__{lang}"),
            display_order=F("feature__display_order"),
            feature_idx=F("feature__idx"),
            feature_name=F(f"feature__name_t9n__{lang}"),
            feature_type=F("feature__feature_type"),
            frontend_input_type=F("feature__frontend_input_type"),
            to_match=F("product__pk"),
        )
        .order_by("to_match")
    )

    for elem in result:
        elem["feature_type"] = FeatureType.labelFromId(elem["feature_type"])
        elem["frontend_input_type"] = FrontendInputType.labelFromId(elem["frontend_input_type"])
        elem["value_txt_t9n"] = elem.pop(f"value_txt_t9n__{lang}")

    return result


# def fetch_pictures(ids, additional_query: List[Q] = None, use_parent_pictures: bool = False):
#     """ Used only in deprecated function -> DEPRECATED """
#
#     if additional_query is None:
#         additional_query = []
#     values_to_fetch = dict(image=F("picture__image"), width=F("picture__width"), height=F("picture__height"))
#     pictures_qs = ProductPicture.objects.filter(*additional_query, product__pk__in=ids)
#     pictures = list(
#         pictures_qs.values("picture_role", "position", to_match=F("product__pk"), **values_to_fetch).order_by(
#             "to_match", "position", "picture__image"
#         )
#     )
#     origin_ids = list(pictures_qs.values_list("picture__pk", flat=True))
#
#     if use_parent_pictures:
#         parent_pictures_qs = ProductPicture.objects.filter(
#             *additional_query, product__productconfigurable__subproduct_links__subproduct__pk__in=ids
#         )
#         parent_pictures = list(
#             parent_pictures_qs.values(
#                 "picture_role",
#                 to_match=F("product__productconfigurable__subproduct_links__subproduct__pk"),
#                 **values_to_fetch
#             ).order_by("to_match", "position", "picture__image")
#         )
#         pictures.extend(parent_pictures)
#         parent_origin_ids = list(parent_pictures_qs.values_list("picture__pk", flat=True))
#         origin_ids.extend(parent_origin_ids)
#
#     thumbs = list(
#         Thumb.objects.filter(origin__pk__in=origin_ids)
#         .values("origin__image", "width", "height", source=F("image"))
#         .order_by("origin__image")
#         .distinct()
#     )
#
#     for idx, elem in enumerate(thumbs):
#         thumbs[idx]["source"] = make_media_url(elem["source"])
#
#     merge_on_match("image", "origin__image", "set", pictures, thumbs)
#
#     # POSTPROCESSING
#     for elem in pictures:
#         elem["picture_role"] = PictureRole.labelFromId(elem["picture_role"])
#         elem["position"] = elem.get("position")
#         elem.pop("width")
#         elem.pop("height")
#
#     return pictures


def fetch_products_name(sku_list: list[str], language, channel) -> dict:
    sku_list = [elem for elem in sku_list]
    query = Product.objects.filter(real_product__sku__in=sku_list, shop__idx=channel.idx).select_related("real_product")
    name_grouped_by_sku = {elem.real_product.sku: elem.name_lang(language) for elem in query}
    return name_grouped_by_sku


def fetch_products_feature(channel_idx: str, sku_list: list[str], feature_idx: str) -> dict:
    query = ProductAttribute.objects.filter(
        product__real_product__sku__in=sku_list, product__shop__idx=channel_idx, feature__idx=feature_idx
    ).select_related("product__real_product")
    return {elem.product.real_product.sku: elem for elem in query}


def skus_have_attribute(channel_idx: str, sku_list, feature_idx: str, filter_attr_value) -> list:
    feature = Feature.objects.get(idx=feature_idx)
    feature_type = feature.feature_type
    if feature_type == FeatureTypeEnum.SELECT:
        pa = ProductAttribute.objects.filter(
            product__real_product__sku__in=sku_list,
            feature__idx=feature_idx,
            product__shop__idx=channel_idx,
            attribute__idx=filter_attr_value,
        ).select_related("product__real_product")
    elif feature_type == FeatureTypeEnum.BOOL:
        filter_attr_value = True if filter_attr_value.lower() == "true" or filter_attr_value == 1 else False
        pa = ProductAttribute.objects.filter(
            product__real_product__sku__in=sku_list,
            feature__idx=feature_idx,
            product__shop__idx=channel_idx,
            value_bool=filter_attr_value,
        ).select_related("product__real_product")
    else:
        raise ValueError("This feature type is not supported")

    return list(pa.values_list("product__real_product__sku", flat=True))


def products_have_attribute(
    channel_idx: str, sku_qty_list: list[SkuQuantityData], feature_idx: str, filter_attr_value
) -> list:
    sku_list = [x.sku for x in sku_qty_list]
    attr_p = skus_have_attribute(channel_idx, sku_list, feature_idx, filter_attr_value)
    return [item for item in sku_qty_list if item.sku in attr_p]
