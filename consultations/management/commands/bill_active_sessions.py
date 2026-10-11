from django.core.management.base import BaseCommand

from consultations.services import bill_all_active_sessions


class Command(BaseCommand):
    help = "Run one billing tick for every in-progress session (schedule this every minute)."

    def handle(self, *args, **options):
        sessions = bill_all_active_sessions()
        ended = [s for s in sessions if s.status == "completed"]
        self.stdout.write(
            self.style.SUCCESS(f"Billed {len(sessions)} active session(s); {len(ended)} ended for insufficient balance.")
        )
