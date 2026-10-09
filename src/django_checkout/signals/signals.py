# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.dispatch import Signal

validate_items_signal = Signal()
validate_vouchers_signal = Signal()
compute_cart_voucher_total_signal = Signal()
order_confirmed_signal = Signal()
order_canceled_signal = Signal()
order_created_signal = Signal()
order_additional_info = Signal()
stock_changed_signal = Signal()
# Emitted after a customer erasure (RODO/GDPR) so downstream modules can scrub
# their own PII rows. Kwargs: email: str, order_ids: list[str], channel_idx: str | None — a pinned erase token
# limits the erasure to that channel (receivers scope to it); None = every channel.
customer_anonymized_signal = Signal()
