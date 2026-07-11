# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import csv
import logging
from typing import AnyStr

import tqdm
from django_accounts.services.accounts_service import read_from_csv

from django_checkout.enums import AssociationChoices, AuthenticationState
from django_checkout.models import PaymentMethod, ShippingMethod

logger = logging.getLogger(__name__)


def read_from_csv(absolute_path: AnyStr) -> (list, AnyStr):
    if absolute_path is not None:
        path = absolute_path
    else:
        return [], "Need to define csv path"
    with open(path) as f:
        data = [{k: v for k, v in row.items()} for row in csv.DictReader(f, skipinitialspace=True)]
    return data, "Success"


def map_column_to_association_type(obj_type):
    match obj_type:
        case "sku":
            association_type = AssociationChoices.SKU
        case "category_idx":
            association_type = AssociationChoices.CATEGORY_IDX
        case "attribute_idx":
            association_type = AssociationChoices.ATTRIBUTE_IDX
        case "attribute_value":
            association_type = AssociationChoices.ATTRIBUTE_VALUE
        case "product_class":
            association_type = AssociationChoices.PRODUCT_CLASS
        case _:
            raise ValueError(f"Association type {obj_type} is not valid")
    return association_type


def map_column_to_authentication_state(obj_authenticaion_state):
    match obj_authenticaion_state:
        case "all":
            association_type = AuthenticationState.ALL
        case "guest":
            association_type = AuthenticationState.GUEST
        case "logged":
            association_type = AuthenticationState.LOGGED
        case _:
            raise ValueError(f"Authentication state {obj_authenticaion_state} is not valid")
    return association_type


def import_product_link_or_limitation(data, object_to_import):
    errors = 0
    number_of_limitations_or_links = 0

    if not data:
        raise ValueError("No data to import")

    for iter_idx, data in tqdm.tqdm(enumerate(data), desc="Importing limitations or links", total=len(data)):
        try:
            idx = data["idx"]
            value = data["value"]
            association_type = data["association_type"]
            shipping_method_code = data["shipping_method"]
            payment_method_code = data["payment_method"]
            authentication_state = data["authentication_state"]
            is_active = data["is_active"]
            number_of_limitations_or_links += 1

            association_type = map_column_to_association_type(association_type)
            association_state = map_column_to_authentication_state(authentication_state)
            shipping_method = ShippingMethod.objects.filter(code=shipping_method_code).first()
            payment_method = PaymentMethod.objects.filter(code=payment_method_code).first()
            if shipping_method_code and not shipping_method:
                logger.error(f"Shipping method with idx {shipping_method_code} not found")
                errors += 1
                continue
            obj, created = object_to_import.objects.update_or_create(
                idx=idx,
                value=value,
                association_type=association_type,
                shipping_method=shipping_method,
                payment_method=payment_method,
                defaults={"is_active": is_active, "authentication_state": association_state},
            )
        except Exception as e:
            logger.error(f"Error during import limitation or link: {e}")
            errors += 1
    return errors, number_of_limitations_or_links


def import_product_link_or_limitation_from_csv(model_to_import, file_path: str):
    data, msg = read_from_csv(file_path)
    errors, number_of_limitations_or_links = import_product_link_or_limitation(data, model_to_import)
    return errors, number_of_limitations_or_links
