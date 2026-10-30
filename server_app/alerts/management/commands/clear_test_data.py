"""
Deletes every incident with its clip, every bus/area with its latest photo, and the
photos cached from incident clips for retraining. Accounts, the AI model and the fleet
GPS feed address are kept. Asks before deleting unless --yes is given.
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from alerts.models import Bus, Incident


class Command(BaseCommand):
    help = "Delete all incidents, clips and buses/areas (keeps accounts and the AI model)"

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="don't ask, just delete")

    def handle(self, *args, **opts):
        incidents, buses = Incident.objects.count(), Bus.objects.count()
        if not opts["yes"]:
            answer = input(f"Delete {incidents} incidents with their clips and {buses} buses/areas? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write("Nothing deleted.")
                return
        Incident.objects.all().delete()
        Bus.objects.all().delete()
        for folder in ("clips", "frames"):
            shutil.rmtree(Path(settings.MEDIA_ROOT) / folder, ignore_errors=True)
        shutil.rmtree(Path(settings.TRAINING_DIR) / "incidents", ignore_errors=True)
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {incidents} incidents and {buses} buses/areas. Add your buses again on the "
            "Buses & areas page."))
