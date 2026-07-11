# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import (
    api_view,
    authenticate,
    parse_body,
    parse_parameters,
    require_authentication,
    require_http_method,
)
from django_utils.api.exceptions import BadRequest, Forbidden, NotFound
from django_utils.api.responses import PaginatedResponse, Response
from django_utils.settings import HEADER_COUNTRY
from process_logger import ProcessLogger

from django_checkout.domain.cart import process_cart
from django_checkout.domain.dto.order import OrderParams, OrderRequest
from django_checkout.domain.validators.order import OrderValidation
from django_checkout.enums import OrderStatus
from django_checkout.models import (
    Cart,
    Channel,
    DiscountCode,
    Invoice,
    Order,
    OrderAttachment,
    OrderStatusLabel,
    ShippingIntent,
)
from django_checkout.settings import CHECKOUT_USE_ISO_DATETIME_FORMAT
from django_checkout.utils.api.decorators import channel_view
from django_checkout.utils.api.utils import paginate, resolve_language
from django_checkout.views.cart import prepare_errors_and_messages
from django_checkout.worker.split_order.split_orders import split_orders_by_attribute
from django_checkout.worker.split_order.validator import check_that_order_can_be_split

from ..bi import Checkout_OrderCreationEvent

logger_process = ProcessLogger("CHECKOUT_CART")


@csrf_exempt
@channel_view
@require_http_method("POST", "GET")
@authenticate
def order_view(request, *args, **kwargs):
    geo_country = request.headers.get(HEADER_COUNTRY, None)

    @require_http_method("POST")
    @parse_body(OrderRequest.Schema)
    def post(request, body: OrderRequest, *args, **kwargs):
        bev = Checkout_OrderCreationEvent(is_ongoing_event=True)
        customer = request.user.customer if (request.user is not None and request.user.is_customer) else None
        # Cart object creation
        try:
            cart_id = body.cart_id
            cart = Cart.objects.get(channel=request.channel, cart_id=body.cart_id, customer=customer)
        except ObjectDoesNotExist as e:
            extra = {"request_body": str(body), "error_message": str(e), "customer": str(customer)}
            logger_process.add_log_param_once("request_body", str(body))
            logger_process.add_log_param_once("customer", str(customer))
            logger_process.error("Order creation failed: cart does not exist.")
            bev.finish_with_error(finish_tag="Order creation failed: cart does not exist.", details=extra)
            raise BadRequest("Cart does not exist")
        except Exception as e:
            extra = {"request_body": str(body), "error_message": str(e), "customer": str(customer)}
            logger_process.add_log_param_once("request_body", str(body))
            logger_process.add_log_param_once("customer", str(customer))
            logger_process.exception(e)
            bev.finish_with_exception(details=extra, e=e)
            raise Forbidden("Unknown error")

        # Processing Cart, Order validation and creation
        processed_data = None
        order = None
        redirect = None
        payment_error = None
        try:
            processed_data, message = process_cart(
                request.channel,
                cart.as_data,
                customer=customer,
                geo_country=geo_country,
                cart=cart,
                is_order_creation=True,
            )
            OrderValidation.validate(cart_id, processed_data)

            # Splitting order
            items_to_split, len_parts = check_that_order_can_be_split(cart, request.channel)

            if not len_parts or not len_parts > 1:
                order, redirect, payment_error = Order.create(
                    processed_data, cart, request.channel, customer=customer, request=request
                )
            split_orders_pretty_id = []
            if len_parts:
                if len_parts > 1:
                    # Jeżeli order może być podzielony na więcej niż jedną część, to dzieli i zapisuje w bazie
                    with transaction.atomic():
                        split_orders_pretty_id, (order, redirect_split, payment_error) = split_orders_by_attribute(
                            processed_data,
                            request.channel,
                            items_to_split,
                            len_parts,
                            customer,
                            request,
                            original_order=order,
                            original_cart=cart,
                        )
                        if redirect_split:
                            redirect = redirect_split
                elif len_parts == 1:
                    # Jeżeli order może być podzielony na jedynie jedną część,
                    # to go nie dzieli i oznacza w zamówieniu głównym, jakim atrybutem miał być dzielony
                    split_by_attr_idx, split_by_feature_idx = tuple(list(items_to_split)[0])
                    cart.cart_body["split_by_attr_idx"] = str(split_by_attr_idx) if split_by_attr_idx else None
                    cart.cart_body["split_by_feature_idx"] = str(split_by_feature_idx)
                    cart.save()

            use_discounts(order.order_body["cart"]["discounts"], request.channel, order=order)
            status_label = order.order_status
            try:
                status_label_obj = OrderStatusLabel.objects.get(status=order.order_status, channel=request.channel)
                available_status_label_languages = status_label_obj.name_t9n if status_label_obj.name_t9n else {}
                if (
                    hasattr(status_label_obj, "name_t9n")
                    and resolve_language(request.channel, request.GET) in available_status_label_languages
                ):
                    status_label = available_status_label_languages.get(resolve_language(request.channel, request.GET))
            except Exception as e:
                logger_process.exception(
                    f"Exception - {e}, maybe add missing status labels to Django_Checkout Order status label: {order.order_status}"
                )
            result = dict(
                order_id=order.order_id,
                order_status=status_label,
                split_orders_pretty_ids=split_orders_pretty_id,
                order_pretty_id=order.pretty_id,
                redirect_url=redirect,
            )
            extra = {
                "order_id": order.order_id,
                "split_orders_pretty_ids": split_orders_pretty_id,
                "order_pretty_id": order.pretty_id,
                "order_status": order.order_status,
                "payment_error": payment_error,
            }
            logger_process.add_log_param_once("order_id", order.order_id)
            logger_process.add_log_param_once("split_orders_pretty_ids", split_orders_pretty_id)
            logger_process.add_log_param_once("order_pretty_id", order.pretty_id)
            logger_process.add_log_param_once("order_status", order.order_status)
            logger_process.add_log_param_once("payment_error", payment_error)

            if settings.DEBUG:
                logger_process.add_log_param_once("redirect_url", redirect)
                logger_process.add_log_param_once("processed_data", processed_data)
                extra["redirect_url"] = redirect  # tylko jesli DEBUG=True, moze zawierac dane wrazliwe
                extra["processed_data"] = processed_data  # tylko jesli DEBUG=True, moze zawierac dane wrazliwe
            logger_process.info("Order was successfully created.")
            bev.finish_with_success(finish_tag="Order was successfully created.", details=extra)
            messages = []
            if message != "":
                messages.append(message)
            if payment_error:
                messages.append("Payment process error. For more info see logs.")
            errors, messages = prepare_errors_and_messages(messages)
            return Response(result, status_code=201, status="CREATED", messages=messages).add_errors(errors)
        except Forbidden as e:
            # OrderValidation.validate raised Forbidden
            extra = {"cart_id": cart_id}
            logger_process.add_log_param_once("cart_id", cart_id)
            logger_process.info("Order creation failed: there was a problem validating the order.")
            bev.finish_with_success(finish_tag="Order creation failed: order is not validating", details=extra)
            raise e
        except Exception as e:
            # unknown exception
            data = ({"validations_status": cart.validation_status, "cart_status": cart.cart_status},)
            status = "INVALID_REQUEST"
            message = f"Unable to create order for cart {cart.cart_id}"
            extra = {
                "cart_id": cart_id,
                "validations_status": cart.validation_status,
                "cart_status": cart.cart_status,
                "request_body": str(body),
                "error_message": str(e),
            }
            logger_process.add_log_param_once("cart_id", cart_id)
            logger_process.add_log_param_once("validations_status", cart.validation_status)
            logger_process.add_log_param_once("cart_status", cart.cart_status)
            logger_process.add_log_param_once("request_body", str(body))
            if settings.DEBUG:
                logger_process.add_log_param_once("processed_data", processed_data)
                extra["processed_data"] = processed_data  # tylko jesli DEBUG=True, moze zawierac dane wrazliwe
            bev.finish_with_exception(details=extra, e=e)
            logger_process.exception(e)
            raise Forbidden(message=message, status=status, data=data)

    @require_http_method("GET")
    @require_authentication
    def get(request, *args, **kwargs):
        def order_to_repr(order):
            order_body = order.order_body
            status_label = None

            try:
                status_label_obj = OrderStatusLabel.objects.get(status=order.order_status, channel=request.channel)
                available_status_label_languages = status_label_obj.name_t9n if status_label_obj.name_t9n else {}
                if (
                    hasattr(status_label_obj, "name_t9n")
                    and resolve_language(request.channel, request.GET) in available_status_label_languages
                ):
                    status_label = available_status_label_languages.get(resolve_language(request.channel, request.GET))
            except Exception as e:
                logger_process.exception(
                    f"Exception - {e}, maybe add missing status labels to Django_Checkout Order status label: {order.order_status}"
                )
            attachments = OrderAttachment.objects.filter(order=order)

            try:
                shipping_intent_obj = ShippingIntent.objects.get(order=order)
                shipping_intent = {
                    "method_code": (
                        shipping_intent_obj.method.code if shipping_intent_obj.method else shipping_intent_obj.code
                    ),
                    "tracking_number": shipping_intent_obj.tracking_number,
                    "tracking_link": shipping_intent_obj.tracking_link,
                }
            except ObjectDoesNotExist:
                shipping_intent = None

            invoices_obj = Invoice.objects.filter(order=order)
            invoices = []
            for invoice in invoices_obj:
                invoices.append(
                    {
                        "invoice_id": str(invoice.invoice_id),
                        "invoice_number": invoice.invoice_number,
                        "invoice_base64": invoice.invoice_base64,
                    }
                )

            order_info = {
                "id": order.pretty_id,
                "status": order.order_status,
                "order_uuid": order.order_id,
                "extra": order.extra,
                "status_label": status_label,
                "attachments": [
                    {"file_id": attachment.pk, "name": attachment.name, "path": attachment.get_download_url}
                    for attachment in attachments
                ],
                "shipping_intent": shipping_intent,
                "invoices": invoices,
                "created": (
                    order.created.isoformat()
                    if CHECKOUT_USE_ISO_DATETIME_FORMAT
                    else order.created.strftime("%Y-%m-%d %H:%M")
                ),
                "updated": (
                    order.updated.isoformat()
                    if CHECKOUT_USE_ISO_DATETIME_FORMAT
                    else order.updated.strftime("%Y-%m-%d %H:%M")
                ),
            }
            updated_order_body = Order.objects.update_shipping_and_payment_method_name(request, order, order_body)
            updated_order_body.update(order_info)
            return order_body

        @parse_parameters(OrderParams.Schema())
        def get_list(request, params: OrderParams, *args, **kwargs):
            sorting_params = []
            if params.sort:
                for sorter in getattr(params, "sort", []):
                    if sorter.field == "created":
                        sorter_direction = "-" if sorter.order == "DESC" else ""
                        sorting_params.append(f"{sorter_direction}{sorter.field}")
            else:
                sorting_params = ["-created"]
            filters = []
            customer = request.user.customer

            if params.order_id:
                orders = Order.objects.filter(channel=request.channel, customer=customer)
                pk = [
                    order.pk
                    for order in orders
                    if params.order_id in order.pretty_id or params.order_id in str(order.order_id)
                ]
                filters.append(Q(pk__in=pk))

            if params.order_status:
                filters.append(Q(order_status=params.order_status))
            if params.product_name:
                orders = Order.objects.filter(channel=request.channel, customer=customer).values_list(
                    "order_body", "pk"
                )
                pk_list_searched = []
                for order in orders:
                    for item in order[0]["cart"].get("items", []):
                        if params.product_name in item.get("name"):
                            pk_list_searched.append(order[1])
                            break
                    else:
                        continue
                filters.append(Q(pk__in=pk_list_searched))

            if params.returnable:
                filters.append(~Q(order_status=OrderStatus.RETURNED))

            orders = Order.objects.filter(channel=request.channel, customer=customer, *filters).order_by(
                *sorting_params
            )
            if params.returnable:
                available_for_return = []
                try:
                    from django_returns.worker.order_return import valid_order_to_return
                except ImportError:
                    raise BadRequest("To get returnable orders you need to install django_returns")
                for order in orders:
                    is_order_return_available, items_available_for_return, msg = valid_order_to_return(order, customer)
                    if is_order_return_available:
                        available_for_return.append(order.pk)
                orders = orders.filter(pk__in=available_for_return)

            pagination, paginated_data = paginate(request.GET, orders)
            res_data = [order_to_repr(order) for order in paginated_data]
            return PaginatedResponse(pagination, res_data)

        def get_record(request, uid=None, *args, **kwargs):
            order: Order = Order.objects.filter(channel=request.channel, pretty_id_snap=uid).first()
            if order is None:
                raise NotFound
            else:
                if request.user is not None:
                    customer = request.user.customer
                    if customer != order.customer:
                        raise Forbidden
                else:
                    raise Forbidden
            res_data = order_to_repr(order)
            return Response(res_data)

        if "uid" in kwargs:
            return get_record(request, *args, **kwargs)
        else:
            return get_list(request, *args, **kwargs)

    if request.method == "GET":
        return get(request, *args, **kwargs)
    else:
        return post(request, *args, **kwargs)


@csrf_exempt
@api_view
@require_http_method("GET")
def get_order_attachment(request, channel_idx=None, order_id=None, file_id=None, uid=None, *args, **kwargs):
    channel = Channel.objects.filter(idx=channel_idx).first()

    try:
        order = Order.objects.get(channel=channel, order_id=order_id)
    except ObjectDoesNotExist as e:
        raise NotFound(message=str(e), status="order_doesnt_exists")

    if str(order.customer.uid) != str(uid):
        e = "Order doesnt belong to user."
        raise Forbidden(message=e, status="order_and_user_unmatched")

    try:
        file = OrderAttachment.objects.get(order=order, pk=file_id)
    except ObjectDoesNotExist as e:
        raise NotFound(message=str(e), status="file_doesnt_exists")

    file_path = file.attachment.path
    if os.path.exists(file_path):
        with open(file_path, "rb") as fh:
            response = HttpResponse(fh.read(), content_type="application/octet-stream")
            response["Content-Disposition"] = "attachment; filename=" + os.path.basename(file_path)
            response["x-filename"] = file.name
            response["Access-Control-Expose-Headers"] = "x-filename"
            return response
    raise NotFound(message="File does't exists", status="file_doesnt_exists")


def use_discounts(discounts: list, channel, order=None):
    """
    Zwiększa licznik użyć kodów rabatowych i zapisuje użycie w tabeli UsedCoupon.

    Args:
        discounts: Lista użytych rabatów z order_body['cart']['discounts']
        channel: Channel w którym utworzono zamówienie
        order: Order object - potrzebny do zapisania UsedCoupon
    """
    from django_checkout.models import UsedCoupon

    for discount in discounts:
        code = discount.get("code")
        if not code:
            continue

        try:
            discount_code_obj = DiscountCode.objects.get(code=code)
            discount_code_obj.current_used = F("current_used") + 1
            discount_code_obj.save()
        except DiscountCode.DoesNotExist:
            # nie dodawaj, bo jest to kod automatyczny, który nie ma ilość użyć
            pass

        if order:
            billing_email = order.billing_email
            shipping_email = order.shipping_email

            if not billing_email:
                if order.customer and hasattr(order.customer, "user"):
                    billing_email = order.customer.user.email
                elif order.order_body.get("addresses", {}).get("billing_address", {}).get("email"):
                    billing_email = order.order_body["addresses"]["billing_address"]["email"]

            if not shipping_email and order.order_body.get("addresses", {}).get("shipping_address", {}).get("email"):
                shipping_email = order.order_body["addresses"]["shipping_address"]["email"]

            if billing_email or shipping_email:
                UsedCoupon.objects.create(
                    channel=channel,
                    order=order,
                    customer=order.customer,
                    billing_email=billing_email or "",
                    shipping_email=shipping_email or "",
                    discount_code=code,
                )
