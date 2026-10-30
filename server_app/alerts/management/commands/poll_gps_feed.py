"""
Updates bus positions from the fleet GPS feed, once or continuously (--loop).

The server also reads the feed automatically while the control room is open; --loop keeps
positions fresh even when nobody has it open.
"""
import time

from django.core.management.base import BaseCommand

from alerts import gps_feed
from alerts.models import GpsFeed


class Command(BaseCommand):
    help = "Update bus positions from the fleet GPS feed"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **opts):
        while True:
            ok, msg = gps_feed.poll_once()
            self.stdout.write(("OK: " if ok else "Problem: ") + msg)
            if not opts["loop"]:
                break
            time.sleep(GpsFeed.get().poll_seconds)
