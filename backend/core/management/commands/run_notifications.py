import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Meeting, MeetingParticipant
from core.notifications import notify

class Command(BaseCommand):
    help = "Deliver scheduled meeting reminders and meeting start notifications."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single reminder check and exit.")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between checks (minimum 10).")

    def handle(self, *args, **options):
        interval = max(10, options["interval"])
        while True:
            self.deliver()
            if options["once"]: return
            time.sleep(interval)

    def deliver(self):
        now = timezone.now()
        soon = Meeting.objects.filter(status=Meeting.Status.SCHEDULED, scheduled_at__gt=now, scheduled_at__lte=now + timedelta(minutes=15)).select_related("host")
        for meeting in soon:
            if meeting.notifications.filter(kind="meeting_reminder").exists(): continue
            recipients = {meeting.host_id: meeting.host}
            for participant in meeting.participants.filter(state=MeetingParticipant.State.ACTIVE).select_related("user"):
                recipients[participant.user_id] = participant.user
            for recipient in recipients.values():
                notify(recipient, meeting=meeting, kind="meeting_reminder", title="Meeting starting soon", body=f"{meeting.title} starts at {meeting.scheduled_at:%H:%M UTC}.")
            self.stdout.write(f"Reminder sent: {meeting.code}")

        due = Meeting.objects.filter(status=Meeting.Status.SCHEDULED, scheduled_at__lte=now).select_related("host")
        for meeting in due:
            meeting.status = Meeting.Status.LIVE
            meeting.started_at = meeting.scheduled_at or now
            meeting.save(update_fields=["status", "started_at"])
            recipient_ids = set(meeting.participants.filter(state="active").values_list("user_id", flat=True)) | {meeting.host_id}
            for user_id in recipient_ids:
                from django.contrib.auth import get_user_model
                user = get_user_model().objects.get(pk=user_id)
                notify(user, meeting=meeting, kind="meeting_started", title="Your meeting is starting", body=f"{meeting.title} is ready to join.")
            self.stdout.write(f"Meeting started: {meeting.code}")
