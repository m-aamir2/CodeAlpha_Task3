import secrets
import string
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

def meeting_code():
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

def upload_path(instance, filename):
    safe_name = filename.replace("/", "_").replace("\\", "_")[:120]
    return f"meetings/{instance.meeting_id}/{secrets.token_hex(8)}-{safe_name}"

class UserProfile(models.Model):
    user = models.OneToOneField(User, related_name="profile", on_delete=models.CASCADE)
    bio = models.CharField(max_length=240, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.user.username

class Meeting(models.Model):
    TRANSLATION_SOURCE_LANGUAGES = (
        ("en-US", "English (United States)"),
        ("ur-PK", "Urdu (Pakistan)"),
        ("zh-CN", "Chinese (Mandarin)"),
        ("ar-SA", "Arabic"),
        ("hi-IN", "Hindi"),
        ("fr-FR", "French"),
        ("es-ES", "Spanish"),
        ("de-DE", "German"),
        ("ja-JP", "Japanese"),
        ("pt-BR", "Portuguese (Brazil)"),
    )
    TRANSLATION_TARGET_LANGUAGES = (
        ("en", "English"), ("ur", "Urdu"), ("zh-Hans", "Chinese (Simplified)"),
        ("ar", "Arabic"), ("hi", "Hindi"), ("fr", "French"), ("es", "Spanish"),
        ("de", "German"), ("ja", "Japanese"), ("pt", "Portuguese"),
    )
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=10, unique=True, default=meeting_code, db_index=True)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hosted_meetings")
    title = models.CharField(max_length=160, default="Untitled meeting")
    description = models.TextField(blank=True, max_length=2000)
    external_url = models.URLField(blank=True, max_length=2048)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.LIVE, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    password_hash = models.CharField(max_length=128, blank=True)
    waiting_room = models.BooleanField(default=False)
    translation_enabled = models.BooleanField(default=False, db_default=False)
    translation_source_language = models.CharField(max_length=12, choices=TRANSLATION_SOURCE_LANGUAGES, default="en-US", db_default="en-US")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-scheduled_at", "-created_at"]
        indexes = [models.Index(fields=["host", "status"]), models.Index(fields=["status", "scheduled_at"])]
    @property
    def duration_seconds(self):
        if not self.started_at: return 0
        return int(((self.ended_at or timezone.now()) - self.started_at).total_seconds())
    def __str__(self): return f"{self.title} ({self.code})"

class MeetingParticipant(models.Model):
    class Role(models.TextChoices):
        HOST = "host", "Host"
        COHOST = "cohost", "Co-host"
        PARTICIPANT = "participant", "Participant"
    class State(models.TextChoices):
        WAITING = "waiting", "Waiting"
        ACTIVE = "active", "Active"
        LEFT = "left", "Left"
        REMOVED = "removed", "Removed"
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meeting_participations")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.PARTICIPANT)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    is_camera_off = models.BooleanField(default=False)
    is_sharing_screen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["meeting", "user"], name="unique_meeting_user")]
        indexes = [models.Index(fields=["meeting", "state"])]

class MeetingMessage(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meeting_messages")
    text = models.CharField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta: ordering = ["created_at"]

class SharedFile(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="files")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_files")
    file = models.FileField(upload_to=upload_path, validators=[FileExtensionValidator(allowed_extensions=["pdf","doc","docx","xls","xlsx","ppt","pptx","png","jpg","jpeg","txt","csv","zip"])])
    original_name = models.CharField(max_length=180)
    size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class WhiteboardSnapshot(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="whiteboard")
    state = models.JSONField(default=dict)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    meeting = models.ForeignKey(Meeting, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    body = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

