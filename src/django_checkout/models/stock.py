# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TypeVar

from django.db import models, transaction
from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from process_logger import ProcessLogger

from django_checkout import settings
from django_checkout.domain.dto.stock import StockReservationData
from django_checkout.enums import OrderStatus
from django_checkout.models.channel import Channel
from django_checkout.models.product_representation import ProductRepresentation
from django_checkout.models.stock_reservation import StockReservation
from django_checkout.models.supplier import Supplier
from django_checkout.signals.signals import stock_changed_signal

from ..bi import Checkout_UpdateStocksFromQmsEvent
from ..settings import CHECKOUT_UPDATE_QUANTITIES_CHUNK_SIZE, GLOBAL_STOCK_DIFFERENT_SUPPLIERS

_T = TypeVar("_T")
logger = ProcessLogger("CHECKOUT_STOCK")


def remove_quantities_for_reservation_object(stock_reservations: list[StockReservationData]):
    for reservation in stock_reservations:
        reservation.stock.decrease(int(reservation.reserved_quantity), commit=True)
        reservation.delete()


class StockManager(models.Manager):
    def update_from_qms_dataset(self, dataset: dict, channel_idx, supplier_code=None) -> None:
        def create_in_chunk():
            if not stocks_to_create:
                return
            logger.add_log_param_once("channel", channel.idx)
            logger.info(f"Checkout Stock Create: channel={channel.idx} progress {cnt} / {total_to_create}")
            self.bulk_create(stocks_to_create_chunk, batch_size=chunk_size)
            report["stocks_created"] += len(stocks_to_create_chunk)

        def update_in_chunk():
            if not stocks_to_update:
                return
            logger.add_log_param_once("channel", channel.idx)
            logger.info(f"Checkout Stock Update: channel={channel.idx} progress {cnt} / {total_to_update}")
            self.bulk_update(stocks_to_update_chunk, fields=["quantity"], batch_size=chunk_size)
            report["stocks_updated"] += len(stocks_to_update_chunk)

            for stock in stocks_to_update_chunk:
                if hasattr(stock, "_original_quantity") and stock._original_quantity != stock.quantity:
                    stock_changed_signal.send(
                        sender=Stock,
                        instance=stock,
                        sku=stock.product.sku,
                        channel=channel.idx,
                        old_quantity=stock._original_quantity,
                        new_quantity=stock.quantity,
                    )
                    stock._original_quantity = stock.quantity

        chunk_size = CHECKOUT_UPDATE_QUANTITIES_CHUNK_SIZE
        channel = Channel.objects.get(idx=channel_idx)
        if supplier_code:
            supplier = Supplier.objects.filter(code=supplier_code, channel=channel).first()
        else:
            supplier = Supplier.objects.filter(channel=channel, is_global=True).first()
        if not supplier:
            raise Exception(f"Supplier not found for channel {channel_idx}")
        logger.add_log_param("channel", channel.idx)
        logger.info("Checkout update_from_qms_dataset is starting")
        bev = Checkout_UpdateStocksFromQmsEvent(channel_idx=channel.idx, is_ongoing_event=True)
        report = {
            "product_representations_existed": None,
            "product_representations_created": None,
            "product_representations_total": None,
            "stocks_existed": None,
            "stocks_created": None,
            "stocks_updated": None,
            "stocks_skipped": None,
            "stocks_reservations_deleted": None,
        }
        try:
            # set dataset skus
            dataset_skus = set()
            for record in dataset["items"]:
                sku = record.get("sku", None)
                if not sku:
                    continue
                dataset_skus.add(sku)

            #
            # ProductRepresentation
            #
            # Ensure ProductRepresentation exists for all dataset items
            logger.info("Checkout is analyzing ProductRepresentations")
            products_sku_to_id = {}
            for product_id, product_sku in ProductRepresentation.objects.filter(channel=channel).values_list(
                "id", "sku"
            ):
                products_sku_to_id[product_sku] = product_id
            report["product_representations_existed"] = len(products_sku_to_id)
            records_to_create = []
            for sku in dataset_skus:
                if sku not in products_sku_to_id:
                    records_to_create.append(ProductRepresentation(sku=sku, channel=channel))
            if records_to_create:
                logger.info(f"Checkout is creating in bulk {len(records_to_create)} ProductRepresentation")
                products = ProductRepresentation.objects.bulk_create(records_to_create)
                for product in products:
                    products_sku_to_id[product.sku] = product.id
                report["product_representations_created"] = len(records_to_create)
            else:
                report["product_representations_created"] = 0
            report["product_representations_total"] = len(products_sku_to_id)

            #
            # Stocks
            #
            logger.info("Checkout is analyzing stocks dataset")
            stocks_by_sku = {}
            for stock in self.filter(product__channel=channel, supplier=supplier).prefetch_related("product"):
                stocks_by_sku[stock.product.sku] = stock
            report["stocks_existed"] = len(stocks_by_sku)

            stocks_to_create = []
            stocks_to_update = []
            report["stocks_skipped"] = 0
            for record in dataset["items"]:
                sku = record.get("sku", None)
                quantity = record.get("quantity", None)
                if not sku or quantity is None:
                    logger.error(f"Invalid record {sku}, {quantity}")
                    continue
                if sku in stocks_by_sku:
                    current = stocks_by_sku[sku]
                    if current.quantity != quantity:
                        current._original_quantity = current.quantity  # Store original for signal
                        current.quantity = quantity
                        stocks_to_update.append(current)
                    else:
                        report["stocks_skipped"] += 1
                else:
                    stocks_to_create.append(
                        self.model(
                            product_id=products_sku_to_id[sku],
                            quantity=quantity,
                            supplier=supplier,
                            saleable_quantity_limit=settings.CHECKOUT_SALEABLE_QUANTITY_LIMIT,
                        )
                    )

            with transaction.atomic():
                report["stocks_created"] = 0
                report["stocks_updated"] = 0
                touched_stocks_ids = set()
                total_to_update = len(stocks_to_update)
                logger.info(f"Checkout bulk stocks update is starting: stocks_to_update={total_to_update}")
                stocks_to_update_chunk = []
                cnt = 0
                for stock in stocks_to_update:
                    cnt += 1
                    touched_stocks_ids.add(stock.id)
                    stocks_to_update_chunk.append(stock)
                    if len(stocks_to_update_chunk) % chunk_size == 0:  # save to disc in bulks
                        update_in_chunk()
                        stocks_to_update_chunk = []
                update_in_chunk()

                total_to_create = len(stocks_to_create)
                logger.info(f"Checkout atomic bulk stocks creation is starting: stocks_to_create={total_to_create} ")
                stocks_to_create_chunk = []
                cnt = 0
                for stock in stocks_to_create:
                    cnt += 1
                    touched_stocks_ids.add(stock.id)
                    stocks_to_create_chunk.append(stock)
                    if len(stocks_to_create_chunk) % chunk_size == 0:  # save to disc in bulks
                        create_in_chunk()
                        stocks_to_create_chunk = []
                create_in_chunk()

                logger.info("Checkout: reservations are released for updated stocks")
                final_statuses = [OrderStatus.COMPLETED, OrderStatus.CANCELED, OrderStatus.RETURNED]
                to_delete = StockReservation.objects.filter(
                    order__order_status__in=final_statuses, stock_id__in=touched_stocks_ids
                )
                nr_deleted, _ = to_delete.delete()
                report["stocks_reservations_deleted"] = nr_deleted

            logger.info("Checkout atomic bulk stocks operations are done")
            bev.finish_with_success(finish_tag="Checkout stocks has been updated from QMS", details=report)
        except Exception as e:
            bev.finish_with_exception(e, details=report)
            raise e


class AnnotatedStockManager(models.Manager):
    def get_queryset(self, saleable_quantity_limit_per_order: bool = True) -> QuerySet[_T]:
        global_stocks = Q(product__channel__idx__in=settings.GLOBAL_STOCK_RESERVATION_CHANNELS)

        global_reserved_quantity = Subquery(
            StockReservation.objects.filter(
                stock__product__sku=OuterRef("product__sku"),
                stock__product__channel__idx__in=settings.GLOBAL_STOCK_RESERVATION_CHANNELS,
            )
            .values("stock__product__sku")
            .annotate(sum_reserved_quantity=Sum("reserved_quantity"))
            .values("sum_reserved_quantity"),
            output_field=models.IntegerField(),
        )

        qs = (
            super()
            .get_queryset()
            .select_related("product", "product__channel")
            .annotate(
                reserved_quantity=Case(
                    When(
                        global_stocks,
                        then=Coalesce(
                            global_reserved_quantity, Coalesce(Sum("stockreservation__reserved_quantity"), Value(0))
                        ),
                    ),
                    default=Coalesce(Sum("stockreservation__reserved_quantity"), Value(0)),
                )
            )
            .annotate(available_stock=F("quantity") - F("reserved_quantity") - F("saleable_quantity_limit"))
        )

        if saleable_quantity_limit_per_order:
            qs = qs.annotate(
                diff=ExpressionWrapper(
                    F("available_stock") - F("saleable_quantity_limit_per_order"), output_field=IntegerField()
                )
            ).annotate(
                saleable_quantity=Case(
                    When(diff__gt=0, then=F("saleable_quantity_limit_per_order")),
                    default=F("available_stock"),
                    output_field=models.IntegerField(),
                )
            )
        else:
            qs = qs.annotate(saleable_quantity=F("available_stock"))

        return qs.annotate(is_saleable=Case(When(saleable_quantity__gt=0, then=Value(True)), default=Value(False)))


class Stock(models.Model):
    objects = StockManager()
    annotated = AnnotatedStockManager()
    product: "ProductRepresentation" = models.ForeignKey(
        "ProductRepresentation", null=False, blank=False, on_delete=models.CASCADE
    )
    supplier = models.ForeignKey("Supplier", null=False, blank=False, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    saleable_quantity_limit_per_order = models.IntegerField(default=999999)
    saleable_quantity_limit = models.IntegerField(default=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_quantity = self.quantity

    def decrease_global(self, quantity: int, commit: bool = False):
        stocks = Stock.objects.filter(
            product__sku=self.product.sku,
            product__channel__idx__in=settings.GLOBAL_STOCK_RESERVATION_CHANNELS,
        )
        if not GLOBAL_STOCK_DIFFERENT_SUPPLIERS:
            stocks = stocks.filter(supplier=self.supplier)

        for stock in stocks:
            logger.info(f"Decreasing global stock {stock.pk} by {quantity}")
            stock.quantity = stock.quantity - quantity
            if commit:
                stock.save()

    def increase_global(self, quantity: int, commit: bool = False):
        stocks = Stock.objects.filter(
            product__sku=self.product.sku,
            product__channel__idx__in=settings.GLOBAL_STOCK_RESERVATION_CHANNELS,
            supplier=self.supplier,
        )
        for stock in stocks:
            stock.quantity = stock.quantity + quantity
            if commit:
                stock.save()

    def increase(self, quantity: int, commit: bool = False):
        if self.product.channel.idx in settings.GLOBAL_STOCK_RESERVATION_CHANNELS:
            self.increase_global(quantity, commit)
        else:
            self.quantity = F("quantity") + quantity
            if commit:
                self.save()

    def check_global_stocks(self):
        if self.product.channel.idx in settings.GLOBAL_STOCK_RESERVATION_CHANNELS:
            stocks = Stock.objects.filter(
                product__sku=self.product.sku,
                product__channel__idx__in=settings.GLOBAL_STOCK_RESERVATION_CHANNELS,
                supplier=self.supplier,
            ).values_list("quantity", flat=True)
            if stocks and len(set(stocks)) != 1:
                logger.error(f"Global stocks are not equal {stocks} for sku {self.product.sku}")

    def decrease(self, quantity: int, commit: bool = False):
        logger.info(f"Decreasing stock for {self.product.channel.idx} by {quantity}")
        if self.product.channel.idx in settings.GLOBAL_STOCK_RESERVATION_CHANNELS:
            self.decrease_global(quantity, commit)
        else:
            self.quantity = F("quantity") - quantity
            if commit:
                self.save()

    def save(self, *args, **kwargs):
        old_quantity = self._original_quantity if hasattr(self, "_original_quantity") else None

        if isinstance(self.quantity, (F, ExpressionWrapper)):
            super().save(*args, **kwargs)
            self.refresh_from_db()
            new_quantity = self.quantity
        else:
            new_quantity = self.quantity
            super().save(*args, **kwargs)

        self.check_global_stocks()

        if old_quantity is not None and old_quantity != new_quantity:
            stock_changed_signal.send(
                sender=self.__class__,
                instance=self,
                sku=self.product.sku,
                channel=self.product.channel.idx,
                old_quantity=old_quantity,
                new_quantity=new_quantity,
            )
        self._original_quantity = new_quantity

    def __str__(self):
        return f"sku:{self.product.sku} - qty:{self.quantity}"
