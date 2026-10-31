from django.contrib.auth.models import User
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import serializers
from urllib.parse import urlsplit
from django.utils import timezone
from .models import Meeting, MeetingMessage, MeetingParticipant, Notification, SharedFile, UserProfile

def user_data(user):
    profile = UserProfile.objects.filter(user=user).first()
    avatar = f"/api/avatars/{user.pk}/" if profile and profile.avatar else ""
    return {"id": user.id, "username": user.username, "name": user.get_full_name() or user.username,
            "email": user.email, "avatar": avatar, "bio": profile.bio if profile else ""}

class RegisterSerializer(serializers.ModelSerializer):
    name = serializers.CharField(write_only=True, max_length=150)
    password = serializers.CharField(write_only=True, min_length=10)
    class Meta:
        model = User
        fields = ("name", "username", "email", "password")
    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists(): raise serializers.ValidationError("An account with this email already exists.")
        return value
    def validate_password(self, value):
        candidate = User(username=self.initial_data.get("username", ""), email=self.initial_data.get("email", ""), first_name=self.initial_data.get("name", ""))
        try: validate_password(value, candidate)
        except ValidationError as exc: raise serializers.ValidationError(exc.messages)
        return value
    def create(self, validated_data):
        name = validated_data.pop("name").strip()
        user = User.objects.create_user(**validated_data, first_name=name)
        return user

class ProfileSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="first_name", required=False, allow_blank=True, max_length=150)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    avatar = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(read_only=True)
    bio = serializers.CharField(source="profile.bio", required=False, allow_blank=True, max_length=240)
    def get_avatar(self, user):
        profile = UserProfile.objects.filter(user=user).first()
        return f"/api/avatars/{user.pk}/" if profile and profile.avatar else ""
    class Meta:
        model = User
        fields = ("id", "username", "email", "name", "bio", "avatar", "date_joined")

class ParticipantSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    class Meta:
        model = MeetingParticipant
        fields = ("id", "user", "role", "state", "joined_at", "left_at", "is_muted", "is_camera_off", "is_sharing_screen")
    def get_user(self, obj): return user_data(obj.user)

class MeetingSerializer(serializers.ModelSerializer):
    host = serializers.SerializerMethodField()
    participant_count = serializers.SerializerMethodField()
    duration_seconds = serializers.IntegerField(read_only=True)
    has_password = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    class Meta:
        model = Meeting
        fields = ("id", "code", "host", "title", "description", "external_url", "status", "scheduled_at", "started_at", "ended_at", "duration_seconds", "waiting_room", "translation_enabled", "translation_source_language", "has_password", "participant_count", "link", "created_at")
        read_only_fields = ("id", "code", "host", "status", "started_at", "ended_at", "created_at")
    def validate_external_url(self, value):
        value = value.strip()
        if not value:
            return ""
        try:
            parsed = urlsplit(value)
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
            parsed = None
        if not parsed or parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or not valid_port:
            raise serializers.ValidationError("Enter a valid HTTPS meeting link.")
        return value
    def get_host(self, obj): return user_data(obj.host)
    def get_participant_count(self, obj): return obj.participants.filter(joined_at__isnull=False).exclude(state=MeetingParticipant.State.REMOVED).count()
    def get_has_password(self, obj): return bool(obj.password_hash)
    def get_link(self, obj):
        origin = settings.FRONTEND_ORIGIN.split(",")[0].rstrip("/")
        return f"{origin}/join/{obj.code}"

class MeetingCreateSerializer(serializers.Serializer):
    title = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=160,
        error_messages={
            "required": "Meeting name is required.",
            "blank": "Meeting name is required.",
        },
    )
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    external_url = serializers.CharField(required=False, allow_blank=True, max_length=2048, default="")
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, max_length=128, default="")
    waiting_room = serializers.BooleanField(required=False, default=False)
    scheduled = serializers.BooleanField(required=False, default=False)
    scheduled_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        error_messages={"invalid": "Enter a valid date and time."},
    )

    def validate_external_url(self, value):
        return MeetingSerializer().validate_external_url(value)

    def validate_password(self, value):
        if value and len(value) < 4:
            raise serializers.ValidationError("Password must be at least 4 characters.")
        return value

    def validate(self, attrs):
        scheduled_at = attrs.get("scheduled_at")
        if attrs.get("scheduled") and not scheduled_at:
            raise serializers.ValidationError({"scheduled_at": "Date & time is required to schedule a meeting."})
        if scheduled_at and scheduled_at <= timezone.now():
            raise serializers.ValidationError({"scheduled_at": "Choose a future date and time."})
        return attrs

class MessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()
    class Meta:
        model = MeetingMessage
        fields = ("id", "sender", "text", "created_at")
    def get_sender(self, obj): return user_data(obj.sender)

class FileSerializer(serializers.ModelSerializer):
    uploader = serializers.SerializerMethodField()
    class Meta:
        model = SharedFile
        fields = ("id", "original_name", "size", "content_type", "uploader", "created_at")
    def get_uploader(self, obj): return user_data(obj.uploader)

class NotificationSerializer(serializers.ModelSerializer):
    meeting_code = serializers.CharField(source="meeting.code", read_only=True, default="")
    class Meta:
        model = Notification
        fields = ("id", "kind", "title", "body", "meeting", "meeting_code", "read_at", "created_at")

