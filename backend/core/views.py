import mimetypes
import os
import time
import codecs
import zipfile
from datetime import timedelta
from uuid import UUID
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import generics, status
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Meeting, MeetingMessage, MeetingParticipant, Notification, SharedFile, UserProfile, WhiteboardSnapshot
from .notifications import notify
from .serializers import FileSerializer, MeetingCreateSerializer, MeetingSerializer, MessageSerializer, NotificationSerializer, ParticipantSerializer, ProfileSerializer, RegisterSerializer, user_data

User = get_user_model()

def member(meeting, user, *, active=True):
    query = MeetingParticipant.objects.filter(meeting=meeting, user=user)
    if active: query = query.filter(state=MeetingParticipant.State.ACTIVE)
    return query.first()

def host_or_cohost(participant):
    return bool(participant and participant.role in (MeetingParticipant.Role.HOST, MeetingParticipant.Role.COHOST))

def detect_safe_file_type(uploaded, extension):
    office_legacy = {".doc", ".xls", ".ppt"}
    office_openxml = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}
    mime_types = {".pdf": "application/pdf", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".txt": "text/plain", ".csv": "text/csv", ".zip": "application/zip"}
    uploaded.seek(0)
    head = uploaded.read(16)
    uploaded.seek(0)
    if extension in (".png", ".jpg", ".jpeg"):
        from PIL import Image
        try:
            image = Image.open(uploaded); image.verify(); fmt = image.format
        except Exception as exc:
            raise ValueError("The image file is damaged or invalid.") from exc
        finally:
            uploaded.seek(0)
        expected = "PNG" if extension == ".png" else "JPEG"
        if fmt != expected: raise ValueError("The image contents do not match the file extension.")
    elif extension == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise ValueError("The file contents are not a valid PDF.")
    elif extension in office_legacy:
        if not head.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            raise ValueError("The file contents do not match a supported Office document.")
    elif extension in office_openxml:
        try:
            with zipfile.ZipFile(uploaded) as archive:
                names = archive.namelist()
                if "[Content_Types].xml" not in names or office_openxml[extension] not in names:
                    raise ValueError("The file contents do not match the selected Office document type.")
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError("The Office document is damaged or invalid.") from exc
        finally:
            uploaded.seek(0)
    elif extension == ".zip":
        if not zipfile.is_zipfile(uploaded):
            uploaded.seek(0); raise ValueError("The file contents are not a valid ZIP archive.")
    elif extension in (".txt", ".csv"):
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        try:
            for chunk in uploaded.chunks():
                if b"\x00" in chunk: raise ValueError("Text files cannot contain binary data.")
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ValueError("Text files must use UTF-8 encoding.") from exc
        finally:
            uploaded.seek(0)
    else:
        raise ValueError("This file type is not supported.")
    uploaded.seek(0)
    return mime_types[extension]

def broadcast(meeting_id, event, data):
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(f"meeting_{meeting_id.hex}", {"type": "meeting.event", "payload": {"event": event, **data}})

async def livekit_moderate_async(meeting_id, user_id, action):
    from livekit import api
    client = api.LiveKitAPI(url=settings.LIVEKIT_URL, api_key=settings.LIVEKIT_API_KEY, api_secret=settings.LIVEKIT_API_SECRET)
    try:
        identity = str(user_id)
        if action == "remove":
            await client.room.remove_participant(api.RoomParticipantIdentity(room=str(meeting_id), identity=identity, revoke_token_ts=int(time.time())))
        elif action == "mute":
            response = await client.room.list_participants(api.ListParticipantsRequest(room=str(meeting_id)))
            participant = next((p for p in response.participants if p.identity == identity), None)
            if participant:
                track = next((t for t in participant.tracks if t.source == 2), None)
                if track:
                    await client.room.mute_published_track(api.MuteRoomTrackRequest(room=str(meeting_id), identity=identity, track_sid=track.sid, muted=True))
    finally:
        await client.aclose()

def livekit_moderate(meeting_id, user_id, action):
    if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        return False
    async_to_sync(livekit_moderate_async)(meeting_id, user_id, action)
    return True

async def livekit_end_async(meeting_id):
    from livekit import api
    client = api.LiveKitAPI(url=settings.LIVEKIT_URL, api_key=settings.LIVEKIT_API_KEY, api_secret=settings.LIVEKIT_API_SECRET)
    try:
        response = await client.room.list_participants(api.ListParticipantsRequest(room=str(meeting_id)))
        for participant in response.participants:
            await client.room.remove_participant(api.RoomParticipantIdentity(room=str(meeting_id), identity=participant.identity, revoke_token_ts=int(time.time())))
        await client.room.delete_room(api.DeleteRoomRequest(room=str(meeting_id)))
    finally:
        await client.aclose()

class AuthThrottle(AnonRateThrottle):
    scope = "auth"

class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request):
        from django.db import connection
        with connection.cursor() as cursor: cursor.execute("SELECT 1")
        return Response({"status": "ok"})

class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        UserProfile.objects.get_or_create(user=user)
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": user_data(user)}, status=201)

class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]
    def post(self, request, *args, **kwargs):
        login_name = str(request.data.get("username", "")).strip()
        if "@" in login_name:
            matched = User.objects.filter(email__iexact=login_name).only("username").first()
            if matched:
                normalized = request.data.copy()
                normalized["username"] = matched.username
                request._full_data = normalized
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            user = User.objects.get(username__iexact=request.data.get("username", ""))
            UserProfile.objects.get_or_create(user=user)
            response.data["user"] = user_data(user)
        return response

class LogoutView(APIView):
    def post(self, request):
        try: RefreshToken(request.data.get("refresh", "")).blacklist()
        except Exception: return Response({"detail": "Invalid refresh token."}, status=400)
        return Response(status=204)

class MeView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    def get(self, request):
        UserProfile.objects.get_or_create(user=request.user)
        return Response(ProfileSerializer(request.user).data)
    def patch(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        name = request.data.get("name")
        if name is not None:
            request.user.first_name = str(name).strip()[:150]
            request.user.save(update_fields=["first_name"])
        if "bio" in request.data: profile.bio = str(request.data.get("bio", ""))[:240]
        if request.FILES.get("avatar"):
            image = request.FILES["avatar"]
            if image.size > 5 * 1024 * 1024: return Response({"detail": "Profile images must be 5 MB or smaller."}, status=400)
            try:
                from PIL import Image
                image.seek(0); decoded = Image.open(image); decoded.verify(); image.seek(0)
                if decoded.format not in {"PNG", "JPEG", "WEBP"}: return Response({"detail": "Upload a PNG, JPEG, or WebP image."}, status=400)
            except Exception: return Response({"detail": "Upload a valid image file."}, status=400)
            profile.avatar = image
        profile.save()
        return Response(ProfileSerializer(request.user).data)

class AvatarView(APIView):
    def get(self, request, user_id):
        owner = get_object_or_404(User, pk=user_id)
        if owner.pk != request.user.pk:
            shared_room = MeetingParticipant.objects.filter(user=request.user, state="active", meeting__participants__user=owner, meeting__participants__state="active").exists()
            if not shared_room: return Response({"detail": "Avatar is unavailable."}, status=404)
        profile = UserProfile.objects.filter(user=owner).first()
        if not profile or not profile.avatar: return Response(status=404)
        mime = mimetypes.guess_type(profile.avatar.name)[0] or "application/octet-stream"
        response = FileResponse(profile.avatar.open("rb"), content_type=mime)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=300"
        return response

class ChangePasswordView(APIView):
    def post(self, request):
        old = request.data.get("old_password", "")
        new = request.data.get("new_password", "")
        if not request.user.check_password(old): return Response({"old_password": ["Current password is incorrect."]}, status=400)
        try: validate_password(new, request.user)
        except ValidationError as exc: return Response({"new_password": list(exc.messages)}, status=400)
        request.user.set_password(new); request.user.save()
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
        for token in OutstandingToken.objects.filter(user=request.user):
            BlacklistedToken.objects.get_or_create(token=token)
        return Response({"detail": "Password updated. Sign in again with your new password."})

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]
    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk)); token = default_token_generator.make_token(user)
            link = f"{settings.FRONTEND_ORIGIN.split(',')[0].rstrip('/')}/reset-password?uid={uid}&token={token}"
            send_mail("Reset your ConnectX password", f"Use this link to reset your password: {link}\n\nIf you did not request this, ignore this email.", settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
        return Response({"detail": "If that email belongs to an account, a reset link has been sent."})

class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]
    def post(self, request):
        try: user = User.objects.get(pk=force_str(urlsafe_base64_decode(request.data.get("uid", ""))))
        except Exception: return Response({"detail": "This password reset link is invalid or expired."}, status=400)
        if not default_token_generator.check_token(user, request.data.get("token", "")):
            return Response({"detail": "This password reset link is invalid or expired."}, status=400)
        try: validate_password(request.data.get("password", ""), user)
        except ValidationError as exc: return Response({"password": list(exc.messages)}, status=400)
        user.set_password(request.data["password"]); user.save()
        return Response({"detail": "Password reset successfully."})

class MeetingListCreateView(APIView):
    def get(self, request):
        qs = Meeting.objects.filter(Q(host=request.user) | Q(participants__user=request.user)).distinct()
        status_filter = request.query_params.get("status")
        search = request.query_params.get("search")
        if status_filter: qs = qs.filter(status=status_filter)
        if search: qs = qs.filter(Q(title__icontains=search) | Q(code__icontains=search) | Q(description__icontains=search))
        return Response(MeetingSerializer(qs, many=True, context={"request": request}).data)
    def post(self, request):
        serializer = MeetingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        values = serializer.validated_data
        scheduled_at = values.get("scheduled_at")
        is_scheduled = values.get("scheduled", False) or scheduled_at is not None
        now = timezone.now()
        from django.contrib.auth.hashers import make_password
        with transaction.atomic():
            meeting = Meeting.objects.create(
                host=request.user,
                title=values["title"],
                description=values["description"],
                external_url=values["external_url"],
                scheduled_at=scheduled_at,
                status=Meeting.Status.SCHEDULED if is_scheduled else Meeting.Status.LIVE,
                started_at=None if is_scheduled else now,
                waiting_room=values["waiting_room"],
                password_hash=make_password(values["password"]) if values["password"] else "",
            )
            MeetingParticipant.objects.create(meeting=meeting, user=request.user, role=MeetingParticipant.Role.HOST,
                state=MeetingParticipant.State.ACTIVE, joined_at=now)
        return Response(MeetingSerializer(meeting, context={"request": request}).data, status=201)

class MeetingDetailView(APIView):
    def get_object(self, pk, user):
        meeting = get_object_or_404(Meeting, pk=pk)
        participant = member(meeting, user, active=False)
        if (not participant or participant.state == MeetingParticipant.State.REMOVED) and meeting.host_id != user.id: return None
        return meeting
    def get(self, request, pk):
        meeting = self.get_object(pk, request.user)
        if not meeting: return Response({"detail": "Meeting not found."}, status=404)
        return Response(MeetingSerializer(meeting, context={"request": request}).data)
    def patch(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if meeting.host_id != request.user.id: return Response({"detail": "Only the host can update meeting settings."}, status=403)
        translation_changed = False
        if "translation_enabled" in request.data:
            enabled = request.data["translation_enabled"]
            if not isinstance(enabled, bool):
                return Response({"translation_enabled": ["Choose whether live translation is on or off."]}, status=400)
            if enabled and not (settings.AZURE_SPEECH_KEY and settings.AZURE_SPEECH_REGION):
                return Response({"detail": "Live translation is not configured. Add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to .env."}, status=503)
            meeting.translation_enabled = enabled
            translation_changed = True
        if "translation_source_language" in request.data:
            source_language = request.data["translation_source_language"]
            allowed_languages = dict(Meeting.TRANSLATION_SOURCE_LANGUAGES)
            if not isinstance(source_language, str) or source_language not in allowed_languages:
                return Response({"translation_source_language": ["Choose a supported speaker language."]}, status=400)
            meeting.translation_source_language = source_language
            translation_changed = True
        for field in ("title", "description", "waiting_room"):
            if field in request.data: setattr(meeting, field, request.data[field])
        if "external_url" in request.data:
            serializer = MeetingSerializer(data={"external_url": request.data.get("external_url", "")}, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=400)
            meeting.external_url = serializer.validated_data["external_url"]
        if "scheduled_at" in request.data:
            from django.utils.dateparse import parse_datetime
            meeting.scheduled_at = parse_datetime(request.data["scheduled_at"]) if request.data["scheduled_at"] else None
        if "password" in request.data:
            from django.contrib.auth.hashers import make_password
            meeting.password_hash = make_password(request.data["password"]) if request.data["password"] else ""
        meeting.save()
        if translation_changed:
            broadcast(meeting.id, "translation_settings", {
                "translation_enabled": meeting.translation_enabled,
                "translation_source_language": meeting.translation_source_language,
            })
        return Response(MeetingSerializer(meeting, context={"request": request}).data)
    def delete(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk, host=request.user)
        try:
            if meeting.status == Meeting.Status.LIVE and settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET:
                async_to_sync(livekit_end_async)(meeting.id)
        except Exception:
            return Response({"detail": "LiveKit could not cancel the active room. Please retry."}, status=503)
        meeting.status = Meeting.Status.CANCELLED; meeting.ended_at = timezone.now(); meeting.save(update_fields=["status", "ended_at"])
        invitees = User.objects.filter(notifications__meeting=meeting, notifications__kind="meeting_invite").distinct()
        for invitee in invitees:
            notify(invitee, meeting=meeting, kind="meeting_cancelled", title="Meeting cancelled", body=f"{meeting.title} has been cancelled by the host.")
        broadcast(meeting.id, "meeting_cancelled", {"title": meeting.title})
        return Response(status=204)

class MeetingLookupView(APIView):
    def get(self, request, code):
        meeting = get_object_or_404(Meeting, code__iexact=code)
        if meeting.status in (Meeting.Status.CANCELLED, Meeting.Status.ENDED):
            return Response({"detail": "This meeting has ended."}, status=410)
        data = MeetingSerializer(meeting, context={"request": request}).data
        data["host"] = {"id": meeting.host_id, "name": meeting.host.get_full_name() or meeting.host.username}
        data.pop("description", None)
        return Response(data)

class MeetingJoinStatusView(APIView):
    def get(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        participant = member(meeting, request.user, active=False)
        if not participant: return Response({"detail": "You have not requested to join this meeting."}, status=404)
        if participant.state == MeetingParticipant.State.REMOVED: return Response({"detail": "You were removed from this meeting."}, status=403)
        return Response({"state": participant.state, "meeting": MeetingSerializer(meeting, context={"request": request}).data})

class MeetingJoinView(APIView):
    def post(self, request, pk):
        try: UUID(str(pk)); query = Q(pk=pk) | Q(code__iexact=str(pk))
        except ValueError: query = Q(code__iexact=str(pk))
        meeting = get_object_or_404(Meeting.objects.select_related("host"), query)
        if meeting.status in (Meeting.Status.ENDED, Meeting.Status.CANCELLED): return Response({"detail": "This meeting has ended."}, status=410)
        if meeting.status == Meeting.Status.SCHEDULED and meeting.scheduled_at and meeting.scheduled_at > timezone.now() and meeting.host_id != request.user.id:
            return Response({"detail": "This meeting has not started yet."}, status=403)
        participant = member(meeting, request.user, active=False)
        if meeting.password_hash and request.user.id != meeting.host_id:
            from django.contrib.auth.hashers import check_password
            if not check_password(request.data.get("password", ""), meeting.password_hash): return Response({"detail": "The meeting password is incorrect."}, status=403)
        if participant and participant.state == MeetingParticipant.State.REMOVED: return Response({"detail": "You were removed from this meeting."}, status=403)
        waiting = meeting.waiting_room and meeting.host_id != request.user.id
        if not participant:
            participant = MeetingParticipant.objects.create(meeting=meeting, user=request.user,
                role=MeetingParticipant.Role.HOST if meeting.host_id == request.user.id else MeetingParticipant.Role.PARTICIPANT,
                state=MeetingParticipant.State.WAITING if waiting else MeetingParticipant.State.ACTIVE,
                joined_at=None if waiting else timezone.now())
        else:
            participant.state = MeetingParticipant.State.WAITING if waiting else MeetingParticipant.State.ACTIVE
            participant.joined_at = None if waiting else timezone.now(); participant.left_at = None; participant.save()
        if waiting:
            notify(meeting.host, meeting=meeting, kind="waiting_room", title="Someone is waiting", body=f"{request.user.get_full_name() or request.user.username} wants to join {meeting.title}.")
            return Response({"waiting": True, "meeting": MeetingSerializer(meeting, context={"request": request}).data}, status=202)
        if meeting.status == Meeting.Status.SCHEDULED:
            meeting.status = Meeting.Status.LIVE; meeting.started_at = timezone.now(); meeting.save(update_fields=["status", "started_at"])
        broadcast(meeting.id, "participant_joined", {"participant": ParticipantSerializer(participant).data})
        if request.user.id != meeting.host_id:
            notify(meeting.host, meeting=meeting, kind="participant_joined", title="Someone joined your meeting", body=f"{request.user.get_full_name() or request.user.username} joined {meeting.title}.")
        return Response({"waiting": False, "meeting": MeetingSerializer(meeting, context={"request": request}).data, "participant": ParticipantSerializer(participant).data})

class MeetingLeaveView(APIView):
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        participant = member(meeting, request.user, active=False)
        if not participant: return Response({"detail": "You are not in this meeting."}, status=403)
        if participant.state not in (MeetingParticipant.State.ACTIVE, MeetingParticipant.State.WAITING): return Response({"detail": "You are not in this meeting."}, status=403)
        participant.state = MeetingParticipant.State.LEFT; participant.left_at = timezone.now(); participant.save(update_fields=["state", "left_at"])
        broadcast(meeting.id, "participant_left", {"user_id": request.user.id, "name": request.user.get_full_name() or request.user.username})
        if request.user.id != meeting.host_id:
            notify(meeting.host, meeting=meeting, kind="participant_left", title="A participant left", body=f"{request.user.get_full_name() or request.user.username} left {meeting.title}.")
        return Response(status=204)

class MeetingEndView(APIView):
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if meeting.host_id != request.user.id: return Response({"detail": "Only the host can end this meeting for everyone."}, status=403)
        if meeting.status != Meeting.Status.ENDED:
            try:
                if settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET:
                    async_to_sync(livekit_end_async)(meeting.id)
            except Exception:
                return Response({"detail": "LiveKit could not end the room. Please retry."}, status=503)
            meeting.status = Meeting.Status.ENDED; meeting.ended_at = timezone.now(); meeting.save(update_fields=["status", "ended_at"])
            meeting.participants.filter(state__in=["active", "waiting"]).update(state="left", left_at=timezone.now())
            broadcast(meeting.id, "meeting_ended", {"title": meeting.title})
        return Response({"status": meeting.status, "ended_at": meeting.ended_at})

class ParticipantListView(APIView):
    def get(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        participant = member(meeting, request.user, active=False)
        if (not participant or participant.state == MeetingParticipant.State.REMOVED) and meeting.host_id != request.user.id: return Response({"detail": "Meeting not found."}, status=404)
        return Response(ParticipantSerializer(meeting.participants.exclude(state="removed").select_related("user"), many=True).data)
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        actor = member(meeting, request.user)
        if meeting.host_id != request.user.id and not host_or_cohost(actor): return Response({"detail": "Host permissions are required."}, status=403)
        action = request.data.get("action")
        participant = get_object_or_404(MeetingParticipant, meeting=meeting, pk=request.data.get("participant_id"))
        if participant.role == MeetingParticipant.Role.HOST: return Response({"detail": "The host cannot be changed through this action."}, status=400)
        if action == "approve":
            participant.state = MeetingParticipant.State.ACTIVE; participant.joined_at = timezone.now(); participant.save()
            broadcast(meeting.id, "participant_approved", {"user_id": participant.user_id})
        elif action == "remove":
            try: livekit_moderate(meeting.id, participant.user_id, "remove")
            except Exception: return Response({"detail": "LiveKit could not remove the participant. Please retry."}, status=503)
            participant.state = MeetingParticipant.State.REMOVED; participant.left_at = timezone.now(); participant.save()
            broadcast(meeting.id, "participant_removed", {"user_id": participant.user_id})
            notify(participant.user, meeting=meeting, kind="participant_removed", title="You were removed from a meeting", body=f"The host removed you from {meeting.title}.")
        elif action == "mute":
            try: livekit_moderate(meeting.id, participant.user_id, "mute")
            except Exception: return Response({"detail": "LiveKit could not mute the participant. Please retry."}, status=503)
            participant.is_muted = True; participant.save(update_fields=["is_muted"])
            broadcast(meeting.id, "participant_muted", {"user_id": participant.user_id})
        elif action == "promote":
            participant.role = MeetingParticipant.Role.COHOST; participant.save(update_fields=["role"])
        else: return Response({"detail": "Supported actions: approve, remove, mute, promote."}, status=400)
        return Response(ParticipantSerializer(participant).data)

class LiveKitTokenView(APIView):
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        participant = member(meeting, request.user)
        if not participant: return Response({"detail": "Join the meeting before requesting a media token."}, status=403)
        if not (settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
            return Response({"detail": "LiveKit is not configured. Add LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET to .env."}, status=503)
        try:
            from livekit import api
            token = api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            token.with_identity(str(request.user.pk)).with_name(request.user.get_full_name() or request.user.username)
            token.with_ttl(timedelta(hours=1))
            token.with_grants(api.VideoGrants(room_join=True, room=str(meeting.id), room_admin=participant.role == MeetingParticipant.Role.HOST, can_publish=True, can_subscribe=True, can_publish_data=True))
            return Response({"url": settings.LIVEKIT_URL, "token": token.to_jwt(), "room": str(meeting.id)})
        except Exception:
            return Response({"detail": "Unable to issue a LiveKit token. Check the LiveKit credentials and backend logs."}, status=503)

class MeetingTranslationTokenView(APIView):
    """Return a short-lived Azure Speech token without exposing the resource key."""
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user):
            return Response({"detail": "Join the meeting before enabling live translation."}, status=403)
        if not meeting.translation_enabled:
            return Response({"detail": "The host has not enabled live translation for this meeting."}, status=409)
        if not (settings.AZURE_SPEECH_KEY and settings.AZURE_SPEECH_REGION):
            return Response({"detail": "Live translation is not configured. Add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to .env."}, status=503)

        region = settings.AZURE_SPEECH_REGION.strip().lower()
        if not region.replace("-", "").isalnum():
            return Response({"detail": "The Azure Speech region setting is invalid."}, status=503)
        cache_key = f"azure-speech-token:{region}"
        token = cache.get(cache_key)
        if not token:
            endpoint = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
            exchange = Request(endpoint, data=b"", headers={"Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY}, method="POST")
            try:
                with urlopen(exchange, timeout=8) as response:
                    token = response.read(8192).decode("utf-8").strip()
                if not token:
                    return Response({"detail": "Azure Speech returned an empty authorization token."}, status=503)
                cache.set(cache_key, token, timeout=8 * 60)
            except (HTTPError, URLError, TimeoutError, OSError):
                return Response({"detail": "Could not connect to Azure Speech. Check the speech key, region, and network."}, status=503)
        return Response({"authorization_token": token, "region": region})

class MessageListCreateView(APIView):
    def get(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        qs = meeting.messages.select_related("sender").order_by("-created_at")[:100]
        return Response(MessageSerializer(reversed(list(qs)), many=True).data)
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        text = str(request.data.get("text", "")).strip()
        if not text or len(text) > 4000: return Response({"text": ["Message must contain 1 to 4000 characters."]}, status=400)
        message = MeetingMessage.objects.create(meeting=meeting, sender=request.user, text=text)
        data = MessageSerializer(message).data
        broadcast(meeting.id, "chat_message", {"message": data})
        recipients = User.objects.filter(meeting_participations__meeting=meeting, meeting_participations__state="active").exclude(id=request.user.id).distinct()
        for recipient in recipients:
            notify(recipient, meeting=meeting, kind="meeting_message", title=f"New message in {meeting.title}", body=f"{request.user.get_full_name() or request.user.username}: {text[:100]}")
        return Response(data, status=201)

class MeetingFileListCreateView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    def get(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        return Response(FileSerializer(meeting.files.select_related("uploader"), many=True).data)
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        uploaded = request.FILES.get("file")
        if not uploaded: return Response({"file": ["Choose a file to upload."]}, status=400)
        if uploaded.size > settings.MAX_UPLOAD_MB * 1024 * 1024: return Response({"file": [f"Files must be {settings.MAX_UPLOAD_MB} MB or smaller."]}, status=400)
        ext = os.path.splitext(uploaded.name)[1].lower()
        allowed_extensions = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".txt", ".csv", ".zip"}
        if ext not in allowed_extensions:
            return Response({"file": ["This file type is not supported."]}, status=400)
        try: verified_type = detect_safe_file_type(uploaded, ext)
        except ValueError as exc: return Response({"file": [str(exc)]}, status=400)
        safe_name = os.path.basename(uploaded.name).replace("\x00", "")[:180]
        obj = SharedFile.objects.create(meeting=meeting, uploader=request.user, file=uploaded, original_name=safe_name, size=uploaded.size, content_type=verified_type)
        broadcast(meeting.id, "file_shared", {"file": FileSerializer(obj).data})
        return Response(FileSerializer(obj).data, status=201)

class FileDownloadView(APIView):
    def get(self, request, file_id):
        obj = get_object_or_404(SharedFile, pk=file_id)
        if not member(obj.meeting, request.user): return Response({"detail": "You are not authorized to download this file."}, status=403)
        response = FileResponse(obj.file.open("rb"), as_attachment=True, filename=obj.original_name, content_type=obj.content_type or "application/octet-stream")
        response["X-Content-Type-Options"] = "nosniff"
        return response
    def delete(self, request, file_id):
        obj = get_object_or_404(SharedFile, pk=file_id)
        actor = member(obj.meeting, request.user)
        if obj.uploader_id != request.user.id and not (actor and host_or_cohost(actor)): return Response({"detail": "Only the uploader or host can delete this file."}, status=403)
        obj.file.delete(save=False); obj.delete()
        return Response(status=204)

class WhiteboardView(APIView):
    def get(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        board, _ = WhiteboardSnapshot.objects.get_or_create(meeting=meeting)
        return Response({"state": board.state, "updated_at": board.updated_at})
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        if not member(meeting, request.user): return Response({"detail": "You are not in this meeting."}, status=403)
        state = request.data.get("state")
        if not isinstance(state, dict) or len(str(state)) > 1_000_000: return Response({"detail": "Whiteboard state is invalid or too large."}, status=400)
        board, _ = WhiteboardSnapshot.objects.update_or_create(meeting=meeting, defaults={"state": state, "updated_by": request.user})
        broadcast(meeting.id, "whiteboard_snapshot", {"state": state, "user_id": request.user.id})
        return Response({"updated_at": board.updated_at})

class NotificationListView(APIView):
    def get(self, request):
        qs = request.user.notifications.select_related("meeting")[:100]
        return Response({"unread": request.user.notifications.filter(read_at__isnull=True).count(), "results": NotificationSerializer(qs, many=True).data})
    def patch(self, request, pk=None):
        if pk is None:
            request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
        else:
            notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
            notification.read_at = timezone.now(); notification.save(update_fields=["read_at"])
        return Response({"detail": "Notifications marked as read."})

class InviteView(APIView):
    def post(self, request, pk):
        meeting = get_object_or_404(Meeting, pk=pk)
        actor = member(meeting, request.user)
        if meeting.host_id != request.user.id and not actor: return Response({"detail": "You are not authorized to invite participants."}, status=403)
        raw_emails = request.data.get("emails", request.data.get("email", []))
        if isinstance(raw_emails, str):
            emails = raw_emails.replace(";", ",").replace("\n", ",").split(",")
        elif isinstance(raw_emails, list):
            emails = raw_emails
        else:
            return Response({"emails": ["Enter one or more email addresses."]}, status=400)
        normalized = []
        for value in emails:
            email = str(value).strip().lower()
            if not email:
                continue
            try:
                validate_email(email)
            except ValidationError:
                return Response({"emails": [f"{email} is not a valid email address."]}, status=400)
            if email not in normalized:
                normalized.append(email)
        link = meeting.external_url or f"{settings.FRONTEND_ORIGIN.split(',')[0].rstrip('/')}/join/{meeting.code}"
        messages = [
            (f"ConnectX meeting invitation: {meeting.title}",
             f"{request.user.get_full_name() or request.user.username} invited you to join {meeting.title}.\n\nMeeting ID: {meeting.code}\nJoin: {link}",
             settings.DEFAULT_FROM_EMAIL, [email])
            for email in normalized
        ]
        if messages:
            try:
                from django.core.mail import send_mass_mail
                send_mass_mail(messages, fail_silently=False)
            except Exception:
                return Response({"detail": "The invitation email could not be sent. Check the email settings and retry."}, status=503)
        if normalized:
            email_filter = Q()
            for email in normalized:
                email_filter |= Q(email__iexact=email)
            invitees = User.objects.filter(email_filter).exclude(id=request.user.id).distinct()
            for invitee in invitees:
                notify(invitee, meeting=meeting, kind="meeting_invite", title="Meeting invitation", body=f"{request.user.get_full_name() or request.user.username} invited you to {meeting.title}.")
        return Response({"detail": f"Invitation sent to {len(normalized)} address(es)." if normalized else "Share the meeting link with participants.", "link": link, "invited": len(normalized)}, status=201)

class StatisticsView(APIView):
    def get(self, request):
        hosted = Meeting.objects.filter(host=request.user)
        past = hosted.filter(status__in=["ended", "cancelled"])
        minutes = sum(m.duration_seconds for m in past) // 60
        upcoming = hosted.filter(status="scheduled", scheduled_at__gte=timezone.now()).count()
        return Response({"hosted_meetings": hosted.count(), "completed_meetings": past.count(), "upcoming_meetings": upcoming, "minutes_hosted": minutes})

