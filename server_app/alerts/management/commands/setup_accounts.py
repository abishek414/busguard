"""
Creates (or resets the passwords of) the starting accounts:

  manager   - system manager (accounts, buses & areas, control room)
  control   - control room checker
  officer1  - a field officer (Sam Taylor)
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from alerts.models import Profile

ACCOUNTS = [
    # username, first name, last name, role, phone
    ("manager", "System", "Manager", Profile.Role.MANAGER, ""),
    ("control", "Control", "Room", Profile.Role.CHECKER, ""),
    ("officer1", "Sam", "Taylor", Profile.Role.OFFICER, "0400 000 001"),
]


class Command(BaseCommand):
    help = "Create the manager, control room and first officer accounts"

    def add_arguments(self, parser):
        parser.add_argument("--password", default="busguard123", help="password for all three accounts")

    def handle(self, *args, **opts):
        User = get_user_model()
        for username, first, last, role, phone in ACCOUNTS:
            user, created = User.objects.get_or_create(username=username)
            user.first_name, user.last_name = first, last
            user.set_password(opts["password"])
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.role, profile.phone = role, phone
            profile.save()
            self.stdout.write(f"{'Created' if created else 'Updated'} {username} ({profile.get_role_display()})")
        self.stdout.write(self.style.SUCCESS(f"Done. Password for these accounts: {opts['password']}"))
