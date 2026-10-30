"""
Saves sample photos from the original fight/normal training videos in training_data/base.
Every retrain mixes these in, so the AI doesn't forget what it learned in its first training.
"""
from django.core.management.base import BaseCommand, CommandError

from alerts import training


class Command(BaseCommand):
    help = "Save sample photos from the original dataset for server retraining"

    def add_arguments(self, parser):
        parser.add_argument("dataset", help="folder with the fight and normal video folders")
        parser.add_argument("--videos-per-class", type=int, default=150)
        parser.add_argument("--frames", type=int, default=4, help="photos per video")

    def handle(self, *args, **opts):
        try:
            training.build_base_dataset(opts["dataset"], opts["videos_per_class"], opts["frames"],
                                        echo=self.stdout.write)
        except ValueError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS("Done. The control room's Retrain AI button is ready."))
