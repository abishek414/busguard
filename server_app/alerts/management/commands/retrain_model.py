"""
Retrains the fight detector from labelled incidents. The control room's "Retrain AI"
button runs this in the background; it can also run on a schedule.
"""
from django.core.management.base import BaseCommand, CommandError

from alerts import training
from alerts.models import TrainingRun


class Command(BaseCommand):
    help = "Retrain the fight detector from labelled bus incidents"

    def add_arguments(self, parser):
        parser.add_argument("--run", type=int, help="(used by the Retrain AI button)")

    def handle(self, *args, **opts):
        if opts["run"]:
            run = TrainingRun.objects.filter(pk=opts["run"]).first()
            if not run:
                raise CommandError("Training run not found")
        else:
            if TrainingRun.objects.filter(status=TrainingRun.Status.RUNNING).exists():
                raise CommandError("A retrain is already running")
            run = TrainingRun.objects.create()
        run = training.retrain(run)
        self.stdout.write(f"{run.get_status_display()}: old {run.old_accuracy} -> new {run.new_accuracy}")
        if run.error:
            self.stdout.write(self.style.ERROR(run.error))
