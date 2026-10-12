# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django_checkout.models import APIKey, Channel
from django_checkout.utils.api_keys import STOREFRONT_SCOPE, access_installed, token_command


class Command(BaseCommand):
    help = "Generate Checkout API-KEY. Key will be shown and saved to given file"

    def add_arguments(self, parser):
        parser.add_argument("channel_idx", type=str, help="Idx of channel in django_checkout")
        parser.add_argument("--file_path", type=str, help="(Optional) path to file where key will be saved")

    def handle(self, *args, **options):
        channel_idx = options["channel_idx"]
        if access_installed():
            raise CommandError(f"Keys are access tokens now: {token_command(STOREFRONT_SCOPE, channel_idx)}")
        file_path = options["file_path"]
        if file_path is None:
            dir_path = os.path.join(settings.DATA_DIR, "tmp/api-key/")
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
            file_path = dir_path + "key"
            self.stdout.write("Default file path where key will be saved: " + file_path)

        try:
            channel: Channel = Channel.objects.get(idx=channel_idx)
            apikey = APIKey(channel=channel)
            apikey.save()

            text_file = open(file_path, "w")
            text_file.write(apikey.key)
            text_file.close()

            self.stdout.write(self.style.SUCCESS("API KEY"))
            self.stdout.write(apikey.key)
        except Exception as ex:
            self.stdout.write(self.style.ERROR(ex))
