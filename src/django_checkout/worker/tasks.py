# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from django.core.management import call_command


@shared_task(name="django_checkout.worker.tasks.cleanup_stranded_carts")
def cleanup_stranded_carts(days: int = 30) -> None:
    """Periodic Celery task delegating to the `cleanup-stranded-carts` management
    command. Scheduled via CELERY_BEAT_SCHEDULE in main/settings.py."""
    call_command("cleanup-stranded-carts", days=days)
