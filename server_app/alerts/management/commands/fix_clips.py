"""
Converts saved incident clips to H.264 so every browser can play them.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from alerts import ai
from alerts.models import Incident


class Command(BaseCommand):
    help = "Convert old incident clips to a format browsers can play"

    def handle(self, *args, **opts):
        fixed = 0
        for inc in Incident.objects.exclude(video=""):
            if not inc.video.name.endswith("_h264.mp4") and os.path.exists(inc.video.path):
                new_path = ai.to_browser_mp4(inc.video.path)
                inc.video.name = os.path.relpath(new_path, settings.MEDIA_ROOT).replace(os.sep, "/")
                inc.save(update_fields=["video"])
                fixed += 1
        self.stdout.write(self.style.SUCCESS(f"Converted {fixed} clips"))
