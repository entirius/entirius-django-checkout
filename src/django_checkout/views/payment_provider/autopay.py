# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import base64
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from autopay_sdk.utils.hash import generate_hash
from django.db import transaction as db_transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from process_logger import ProcessLogger

from django_checkout import settings
from django_checkout.bi import Checkout_AutopayReturnEvent
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import PaymentIntent

logger = ProcessLogger("PAYMENT_AUTOPAY_PROVIDER_NOTIFY")


def generate_confirmation_xml(service_id: str, order_id: str, confirmation: str, hash_value: str) -> str:
    # Tworzenie struktury XML dla potwierdzenia
    confirmation_list = ET.Element("confirmationList")
    service_id_element = ET.SubElement(confirmation_list, "serviceID")
    service_id_element.text = service_id

    transactions_confirmations = ET.SubElement(confirmation_list, "transactionsConfirmations")
    transaction_confirmed = ET.SubElement(transactions_confirmations, "transactionConfirmed")
    order_id_element = ET.SubElement(transaction_confirmed, "orderID")
    order_id_element.text = order_id
    confirmation_element = ET.SubElement(transaction_confirmed, "confirmation")
    confirmation_element.text = confirmation

    hash_element = ET.SubElement(confirmation_list, "hash")
    hash_element.text = hash_value

    # Konwersja struktury XML do stringa
    confirmation_xml = ET.tostring(confirmation_list, encoding="UTF-8").decode("utf-8")
    return confirmation_xml


def extract_from_xml(root, separator="|"):
    """
    Przetwarza XML, tworząc słownik z nazw elementów i ich wartości
    zgodnie z kolejnością w dokumencie. Jeżeli element nie istnieje
    lub brak mu wartości, jest pomijany.

    Element hash jest pomijany, bo nie wchodzi do obliczania skrótu.
    Zagnieżdżone elementy są reprezentowane jako klucze w formacie
    'parent->child' zgodnie z dokumentacją Autopay.

    :param root: Element XML (root)
    :param separator: Separator (nieużywany, zachowany dla kompatybilności)
    :return: Słownik {nazwa_tagu: wartość} dla wszystkich elementów oprócz 'hash'
    """

    def visit_node(node, parent_key=""):
        result = {}

        # Jeżeli węzeł ma tekst i nie jest hashem
        if node.tag != "hash" and node.text and node.text.strip():
            # Użyj nazwy tagu jako klucza
            key = f"{parent_key}->{node.tag}" if parent_key else node.tag
            result[key] = node.text.strip()

        # Przejdź przez wszystkie dzieci
        for child in node:
            # Dla zagnieżdżonych elementów przekaż nazwę rodzica
            child_parent = node.tag if node.tag != root.tag else ""
            child_results = visit_node(child, child_parent)
            result.update(child_results)

        return result

    return visit_node(root)


@csrf_exempt
@api_view
@require_http_method("POST")
def autopay_return(request: HttpRequest, channel_idx=None, *args, **kwargs) -> HttpResponse:
    """Funkcja autopay_return() obsługuje żądania POST zawierające dane XML od AutoPay."""
    bev = Checkout_AutopayReturnEvent(is_ongoing_event=True)
    xml_data = None

    try:
        raw_body = request.body.decode("utf-8")
        if "transactions=" in raw_body:
            encoded_data = raw_body.replace("transactions=", "", 1)
        else:
            logger.error("Nie znaleziono klucza 'transactions=' w danych")
            raise ValueError("Nie znaleziono klucza 'transactions=' w danych")

        url_decoded_data = unquote(encoded_data)
        try:
            xml_data = base64.b64decode(url_decoded_data).decode("utf-8")
        except Exception as e:
            logger.exception(e)
            raise ValueError("Błąd dekodowania Base64: dane są niepoprawne") from e

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            logger.exception(e)
            raise ValueError("Błąd parsowania XML") from e
        logger.add_log_param("body", xml_data)
        # Przetwarzanie danych transakcji
        service_id = root.find(".//serviceID").text
        transactions = root.findall(".//transaction")

        for transaction in transactions:
            order_id = transaction.find(".//orderID").text
            remote_id = transaction.find(".//remoteID").text
            amount = float(transaction.find(".//amount").text)
            currency = transaction.find(".//currency").text
            # Opcjonalne pola - mogą nie występować w niektórych statusach (np. PENDING)
            gateway_id_element = transaction.find(".//gatewayID")
            gateway_id = gateway_id_element.text if gateway_id_element is not None else None
            payment_date_element = transaction.find(".//paymentDate")
            payment_date = payment_date_element.text if payment_date_element is not None else None
            payment_status = transaction.find(".//paymentStatus").text
            payment_status_details_element = transaction.find(".//paymentStatusDetails")
            payment_status_details = (
                payment_status_details_element.text if payment_status_details_element is not None else None
            )
            # Weryfikacja hash
            received_hash = root.find(".//hash").text
            generate_hash_args = extract_from_xml(root)
            calculated_hash = generate_hash(
                channel=channel_idx, hash_key=settings.AUTOPAY_HASH_KEY, **generate_hash_args
            )

            extra = {
                "service_id": service_id,
                "order_id": order_id,
                "remote_id": remote_id,
                "amount": amount,
                "currency": currency,
                "gateway_id": gateway_id,
                "payment_date": payment_date,
                "payment_status": payment_status,
                "payment_status_details": payment_status_details,
                "is_status_changed": None,
                "body": xml_data,
            }

            if received_hash != calculated_hash:
                logger.add_log_param("extra", extra)
                logger.error("Hash verification failed")
                return HttpResponse(
                    generate_confirmation_xml(service_id, order_id, "NOTCONFIRMED", received_hash),
                    content_type="text/xml",
                    status=400,
                )

            with db_transaction.atomic():
                payment_intent = (
                    PaymentIntent.objects.select_for_update()
                    .select_related("order")
                    .filter(external_order_id=order_id)
                    .first()
                )
                if not payment_intent:
                    extra = {"order_id": order_id, "autopay_response": xml_data, "is_status_changed": False}
                    logger.set_log_params({"details": extra})
                    logger.error("There is no PaymentIntent object with the given order ID from AutoPay")
                    bev.finish_with_error(finish_tag="Cannot find related PaymentIntent object", details=extra)
                    return HttpResponse("For more information see log", status=500)

                # Aktualizacja statusu płatności
                if payment_status == "SUCCESS":
                    payment_intent.payment_status = PaymentIntentStatus.COMPLETE
                    payment_intent.order.order_status = OrderStatus.CONFIRMED
                    confirmation = "CONFIRMED"
                    extra["is_status_changed"] = True
                    logger.add_log_param("is_status_changed", True)
                    logger.info("PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED")
                    bev.finish_with_success(
                        finish_tag="PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED",
                        details=extra,
                    )

                elif payment_status == "PENDING":
                    if payment_intent.payment_status == PaymentIntentStatus.COMPLETE:
                        confirmation = "CONFIRMED"
                        extra["is_status_changed"] = False
                        logger.add_log_param("is_status_changed", False)
                        logger.warning(
                            f"PaymentIntent already COMPLETE, ignoring PENDING status from Autopay. "
                            f"Order ID: {order_id}, Remote ID: {remote_id}"
                        )
                        bev.finish_with_success(
                            finish_tag="PaymentIntent already COMPLETE, PENDING status ignored",
                            details=extra,
                        )
                    else:
                        payment_intent.payment_status = PaymentIntentStatus.PENDING
                        confirmation = "CONFIRMED"
                        extra["is_status_changed"] = True
                        logger.add_log_param("is_status_changed", True)
                        logger.info("PaymentIntent status is set to PENDING")
                        bev.finish_with_success(finish_tag="PaymentIntent status is set to PENDING", details=extra)

                elif payment_status == "FAILURE":
                    # KRYTYCZNE: Nie zmieniaj statusu z COMPLETE na ERROR!
                    if payment_intent.payment_status == PaymentIntentStatus.COMPLETE:
                        confirmation = "CONFIRMED"
                        extra["is_status_changed"] = False
                        logger.add_log_param("is_status_changed", False)
                        logger.critical(
                            f"PaymentIntent already COMPLETE, ignoring FAILURE status from Autopay! "
                            f"Order ID: {order_id}, Remote ID: {remote_id}, "
                            f"Failure reason: {payment_status_details}. "
                            f"MANUAL REVIEW REQUIRED - goods may have been shipped!"
                        )
                        bev.finish_with_error(
                            finish_tag="PaymentIntent already COMPLETE, FAILURE status ignored - MANUAL REVIEW REQUIRED",
                            details=extra,
                        )
                    else:
                        payment_intent.payment_status = PaymentIntentStatus.ERROR
                        confirmation = "CONFIRMED"
                        extra["is_status_changed"] = True
                        logger.add_log_param("is_status_changed", True)
                        logger.info("PaymentIntent status is set to FAILURE")
                        bev.finish_with_error(finish_tag="PaymentIntent status is set to FAILURE", details=extra)
                else:
                    payment_intent.payment_status = PaymentIntentStatus.ERROR
                    extra["is_status_changed"] = True
                    logger.add_log_param("is_status_changed", True)
                    logger.error(f"Unknown payment status: {payment_status}")
                    bev.finish_with_error(finish_tag="Unknown payment status", details=extra)
                # Zapisanie PaymentIntent i powiązanego zamówienia — terminalność COMPLETE wymusza baza:
                # warunek w UPDATE jest sprawdzany względem zacommitowanego wiersza, więc opłaconej
                # płatności nie cofnie ani nieświeży odczyt, ani ponowiony/równoległy ITN.
                if payment_intent.payment_status == PaymentIntentStatus.COMPLETE:
                    payment_intent.save(update_fields=["payment_status"])
                    # in_status_since ustawia Order.save() przy zmianie statusu, updated jest auto_now —
                    # oba muszą być w update_fields, żeby zostały utrwalone
                    payment_intent.order.save(update_fields=["order_status", "in_status_since", "updated"])
                else:
                    rows_updated = (
                        PaymentIntent.objects.filter(pk=payment_intent.pk)
                        .exclude(payment_status=PaymentIntentStatus.COMPLETE)
                        .update(payment_status=payment_intent.payment_status)
                    )
                    if rows_updated == 0:
                        logger.critical(
                            f"Refused to downgrade COMPLETE PaymentIntent (DB row already COMPLETE). "
                            f"Order ID: {order_id}, Remote ID: {remote_id}, ITN status: {payment_status}. "
                            f"MANUAL REVIEW REQUIRED!"
                        )
            hash_value = generate_hash(
                channel=channel_idx,
                hash_key=settings.AUTOPAY_HASH_KEY,
                serviceID=service_id,
                orderId=order_id,
                confirmation=confirmation,
            )

            confirmation_xml = generate_confirmation_xml(service_id, order_id, confirmation, hash_value)

        return HttpResponse(confirmation_xml, content_type="text/xml", status=200)

    except Exception as e:
        logger.add_log_param("body", xml_data)
        logger.exception(e)
        return HttpResponse("An error occurred while processing the XML request", status=500)
