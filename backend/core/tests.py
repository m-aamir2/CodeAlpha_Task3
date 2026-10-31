from datetime import timedelta
from unittest.mock import patch
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from .models import Meeting, MeetingMessage, MeetingParticipant, Notification, SharedFile
from connectx.asgi import application

class ConnectXAPITests(APITestCase):
    def setUp(self):
        self.host = User.objects.create_user(username="host", email="host@example.test", password="StrongPassphrase!345")
        self.guest = User.objects.create_user(username="guest", email="guest@example.test", password="OtherStrongPassphrase!67")
        self.host_client = APIClient(); self.host_client.force_authenticate(self.host)
        self.guest_client = APIClient(); self.guest_client.force_authenticate(self.guest)

    def create_meeting(self, **options):
        payload = {"title": "Weekly planning", **options}
        response = self.host_client.post("/api/meetings/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_meeting_creation_validates_required_fields_before_writing(self):
        initial_count = Meeting.objects.count()
        missing_name = self.host_client.post("/api/meetings/", {"title": "   "}, format="json")
        self.assertEqual(missing_name.status_code, 400)
        self.assertIn("title", missing_name.data)
        self.assertIn("Meeting name is required.", str(missing_name.data["title"]))

        missing_date = self.host_client.post("/api/meetings/", {"title": "Planning", "scheduled": True}, format="json")
        self.assertEqual(missing_date.status_code, 400)
        self.assertIn("scheduled_at", missing_date.data)
        self.assertIn("Date & time is required", str(missing_date.data["scheduled_at"]))

        invalid_date = self.host_client.post("/api/meetings/", {
            "title": "Planning", "scheduled": True, "scheduled_at": "not-a-date"
        }, format="json")
        self.assertEqual(invalid_date.status_code, 400)
        self.assertIn("scheduled_at", invalid_date.data)

        past_date = self.host_client.post("/api/meetings/", {
            "title": "Planning", "scheduled": True, "scheduled_at": (timezone.now() - timedelta(minutes=1)).isoformat()
        }, format="json")
        self.assertEqual(past_date.status_code, 400)
        self.assertIn("Choose a future date and time.", str(past_date.data["scheduled_at"]))
        self.assertEqual(Meeting.objects.count(), initial_count)

    def test_scheduled_meeting_persists_options_and_appears_in_meeting_list(self):
        starts_at = timezone.now() + timedelta(days=2)
        response = self.host_client.post("/api/meetings/", {
            "title": "  Product planning  ",
            "description": "Review launch milestones.",
            "external_url": "https://meet.google.com/abc-defg-hij",
            "password": "planning-secret",
            "waiting_room": True,
            "scheduled": True,
            "scheduled_at": starts_at.isoformat(),
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["title"], "Product planning")
        self.assertEqual(response.data["status"], Meeting.Status.SCHEDULED)
        self.assertTrue(response.data["waiting_room"])
        self.assertTrue(response.data["has_password"])

        saved = Meeting.objects.get(pk=response.data["id"])
        self.assertEqual(saved.description, "Review launch milestones.")
        self.assertEqual(saved.external_url, "https://meet.google.com/abc-defg-hij")
        self.assertEqual(saved.scheduled_at, starts_at)
        self.assertTrue(check_password("planning-secret", saved.password_hash))
        self.assertIsNone(saved.started_at)
        self.assertTrue(MeetingParticipant.objects.filter(meeting=saved, user=self.host, role=MeetingParticipant.Role.HOST).exists())

        listed = self.host_client.get("/api/meetings/")
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(row["id"] == response.data["id"] for row in listed.data))

    def test_instant_meeting_is_created_live_with_optional_fields_defaulted(self):
        response = self.host_client.post("/api/meetings/", {
            "title": "Instant team sync", "scheduled": False, "scheduled_at": None,
            "description": "", "external_url": "", "password": "", "waiting_room": False,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Meeting.Status.LIVE)
        self.assertFalse(response.data["waiting_room"])
        self.assertFalse(response.data["has_password"])
        saved = Meeting.objects.get(pk=response.data["id"])
        self.assertIsNone(saved.scheduled_at)
        self.assertIsNotNone(saved.started_at)
        self.assertEqual(saved.external_url, "")
        self.assertTrue(MeetingParticipant.objects.filter(meeting=saved, user=self.host, state=MeetingParticipant.State.ACTIVE).exists())

    def test_live_translation_requires_provider_configuration_and_host_permission(self):
        meeting = self.create_meeting()
        with override_settings(AZURE_SPEECH_KEY="", AZURE_SPEECH_REGION=""):
            missing_provider = self.host_client.patch(f"/api/meetings/{meeting['id']}/", {
                "translation_enabled": True,
            }, format="json")
        self.assertEqual(missing_provider.status_code, 503)
        self.assertFalse(Meeting.objects.get(pk=meeting["id"]).translation_enabled)

        with override_settings(AZURE_SPEECH_KEY="speech-test-key", AZURE_SPEECH_REGION="eastus"):
            forbidden = self.guest_client.patch(f"/api/meetings/{meeting['id']}/", {
                "translation_enabled": True,
            }, format="json")
            self.assertEqual(forbidden.status_code, 403)
            invalid_language = self.host_client.patch(f"/api/meetings/{meeting['id']}/", {
                "translation_source_language": "unsupported-locale",
            }, format="json")
            self.assertEqual(invalid_language.status_code, 400)
            enabled = self.host_client.patch(f"/api/meetings/{meeting['id']}/", {
                "translation_enabled": True, "translation_source_language": "ur-PK",
            }, format="json")
            self.assertEqual(enabled.status_code, 200, enabled.data)
            self.assertTrue(enabled.data["translation_enabled"])
            self.assertEqual(enabled.data["translation_source_language"], "ur-PK")

    def test_translation_token_is_short_lived_server_minted_and_requires_active_member(self):
        meeting = self.create_meeting()
        disabled = self.host_client.post(f"/api/meetings/{meeting['id']}/translation-token/")
        self.assertEqual(disabled.status_code, 409)

        with override_settings(AZURE_SPEECH_KEY="speech-test-key", AZURE_SPEECH_REGION="testtranslationregion"):
            self.host_client.patch(f"/api/meetings/{meeting['id']}/", {
                "translation_enabled": True,
            }, format="json")
            guest = self.guest_client.post(f"/api/meetings/{meeting['id']}/translation-token/")
            self.assertEqual(guest.status_code, 403)

            cache.delete("azure-speech-token:testtranslationregion")
            with patch("core.views.cache.set") as cache_set, patch("core.views.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b"short-lived-speech-token"
                response = self.host_client.post(f"/api/meetings/{meeting['id']}/translation-token/")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data, {"authorization_token": "short-lived-speech-token", "region": "testtranslationregion"})
            self.assertEqual(urlopen.call_args.args[0].full_url, "https://testtranslationregion.api.cognitive.microsoft.com/sts/v1.0/issueToken")
            self.assertEqual(urlopen.call_args.args[0].get_header("Ocp-apim-subscription-key"), "speech-test-key")
            self.assertEqual(cache_set.call_args.kwargs["timeout"], 8 * 60)

    def test_registration_and_email_login(self):
        response = self.client.post("/api/auth/register/", {"name": "Taylor Reed", "username": "taylor", "email": "TAYLOR@example.test", "password": "Taylor-Long-Unique-348!"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)
        self.assertTrue(User.objects.get(username="taylor").check_password("Taylor-Long-Unique-348!"))
        login = self.client.post("/api/auth/login/", {"username": "taylor@example.test", "password": "Taylor-Long-Unique-348!"}, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(login.data["user"]["username"], "taylor")

    def test_rotating_refresh_tokens_keep_the_session_valid(self):
        login = self.client.post("/api/auth/login/", {
            "username": "host", "password": "StrongPassphrase!345"
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        first_refresh = login.data["refresh"]

        refreshed = self.client.post("/api/auth/refresh/", {"refresh": first_refresh}, format="json")
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertNotEqual(refreshed.data["refresh"], first_refresh)
        self.assertTrue(refreshed.data["access"])

        refreshed_again = self.client.post("/api/auth/refresh/", {"refresh": refreshed.data["refresh"]}, format="json")
        self.assertEqual(refreshed_again.status_code, 200, refreshed_again.data)
        self.assertTrue(refreshed_again.data["access"])

    @override_settings(LIVEKIT_URL="", LIVEKIT_API_KEY="", LIVEKIT_API_SECRET="")
    def test_meeting_id_join_chat_permissions_and_history(self):
        meeting = self.create_meeting()
        self.assertEqual(meeting["status"], "live")
        joined = self.guest_client.post(f"/api/meetings/{meeting['code']}/join/", {}, format="json")
        self.assertEqual(joined.status_code, 200, joined.data)
        message = self.guest_client.post(f"/api/meetings/{meeting['id']}/messages/", {"text": "Good morning"}, format="json")
        self.assertEqual(message.status_code, 201)
        self.assertEqual(MeetingMessage.objects.filter(meeting_id=meeting["id"]).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.host, kind="meeting_message").exists())
        stranger = User.objects.create_user(username="stranger", password="AnotherStrongPassphrase!123")
        stranger_client = APIClient(); stranger_client.force_authenticate(stranger)
        self.assertEqual(stranger_client.get(f"/api/meetings/{meeting['id']}/messages/").status_code, 403)
        self.assertEqual(self.host_client.post(f"/api/meetings/{meeting['id']}/end/", {}, format="json").status_code, 200)
        self.assertEqual(self.guest_client.get(f"/api/meetings/{meeting['id']}/").status_code, 200)
        self.assertEqual(self.guest_client.get(f"/api/meetings/{meeting['id']}/participants/").status_code, 200)
        self.assertEqual(self.guest_client.post(f"/api/meetings/{meeting['id']}/token/", {}, format="json").status_code, 403)

    def test_public_invitation_lookup_does_not_disclose_meeting_description(self):
        meeting = self.create_meeting(description="Private planning notes")
        response = self.guest_client.get(f"/api/meetings/lookup/{meeting['code']}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("description", response.data)
        self.assertNotIn("Private planning notes", str(response.data))

    def test_external_meeting_link_is_validated_and_persisted(self):
        url = "https://meet.google.com/abc-defg-hij"
        meeting = self.create_meeting(external_url=url)
        self.assertEqual(meeting["external_url"], url)
        self.assertEqual(Meeting.objects.get(pk=meeting["id"]).external_url, url)
        invalid = self.host_client.post("/api/meetings/", {"title": "Bad link", "external_url": "javascript:alert(1)"}, format="json")
        self.assertEqual(invalid.status_code, 400)
        changed = self.host_client.patch(f"/api/meetings/{meeting['id']}/", {"external_url": "http://meet.google.com/abc"}, format="json")
        self.assertEqual(changed.status_code, 400)

    def test_creator_can_invite_multiple_emails_with_external_join_url(self):
        external_url = "https://zoom.us/j/123456789"
        meeting = self.create_meeting(external_url=external_url)
        response = self.host_client.post(f"/api/meetings/{meeting['id']}/invite/", {
            "emails": ["guest@example.test", "new.person@example.test", "GUEST@example.test"]
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["invited"], 2)
        self.assertEqual(response.data["link"], external_url)
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(all(external_url in message.body for message in mail.outbox))
        self.assertTrue(Notification.objects.filter(recipient=self.guest, meeting_id=meeting["id"], kind="meeting_invite").exists())
        invalid = self.host_client.post(f"/api/meetings/{meeting['id']}/invite/", {"emails": ["bad-address"]}, format="json")
        self.assertEqual(invalid.status_code, 400)

    def test_pdf_upload_is_listed_downloadable_and_deletable(self):
        meeting = self.create_meeting()
        pdf = b"%PDF-1.1\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n%%EOF\n"
        upload = SimpleUploadedFile("agenda.pdf", pdf, content_type="application/pdf")
        result = self.host_client.post(f"/api/meetings/{meeting['id']}/files/", {"file": upload}, format="multipart")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["original_name"], "agenda.pdf")
        self.assertEqual(result.data["content_type"], "application/pdf")
        file_id = result.data["id"]
        listed = self.host_client.get(f"/api/meetings/{meeting['id']}/files/")
        self.assertEqual([row["id"] for row in listed.data], [file_id])
        download = self.host_client.get(f"/api/files/{file_id}/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(download.streaming_content), pdf)
        deleted = self.host_client.delete(f"/api/files/{file_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(SharedFile.objects.filter(pk=file_id).exists())

    def test_participant_list_and_counts_support_more_than_one_hundred(self):
        meeting = self.create_meeting(title="Large room")
        User.objects.bulk_create([
            User(username=f"large_room_{index}", password="!", email=f"large{index}@example.test")
            for index in range(125)
        ])
        guests = User.objects.filter(username__startswith="large_room_")
        now = timezone.now()
        MeetingParticipant.objects.bulk_create([
            MeetingParticipant(meeting_id=meeting["id"], user=user, state=MeetingParticipant.State.ACTIVE, joined_at=now)
            for user in guests
        ])
        rows = self.host_client.get(f"/api/meetings/{meeting['id']}/participants/")
        self.assertEqual(rows.status_code, 200)
        self.assertEqual(len(rows.data), 126)
        listed = self.host_client.get("/api/meetings/")
        self.assertEqual(listed.data[0]["participant_count"], 126)

    def test_meeting_password_and_waiting_room_approval(self):
        locked = self.create_meeting(password="join-please")
        wrong = self.guest_client.post(f"/api/meetings/{locked['code']}/join/", {"password": "wrong"}, format="json")
        self.assertEqual(wrong.status_code, 403)
        right = self.guest_client.post(f"/api/meetings/{locked['code']}/join/", {"password": "join-please"}, format="json")
        self.assertEqual(right.status_code, 200)

        waiting = self.create_meeting(title="Review room", waiting_room=True)
        pending = self.guest_client.post(f"/api/meetings/{waiting['code']}/join/", {}, format="json")
        self.assertEqual(pending.status_code, 202)
        self.assertTrue(pending.data["waiting"])
        row = MeetingParticipant.objects.get(meeting_id=waiting["id"], user=self.guest)
        approved = self.host_client.post(f"/api/meetings/{waiting['id']}/participants/", {"participant_id": row.pk, "action": "approve"}, format="json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.guest_client.get(f"/api/meetings/{waiting['id']}/join/status/").data["state"], "active")

    def test_file_download_requires_membership_and_whiteboard_persists(self):
        meeting = self.create_meeting()
        self.guest_client.post(f"/api/meetings/{meeting['id']}/join/", {}, format="json")
        upload = SimpleUploadedFile("notes.txt", b"ConnectX shared note", content_type="text/plain")
        result = self.guest_client.post(f"/api/meetings/{meeting['id']}/files/", {"file": upload}, format="multipart")
        self.assertEqual(result.status_code, 201, result.data)
        file_id = result.data["id"]
        self.assertEqual(self.host_client.get(f"/api/files/{file_id}/").status_code, 200)
        state = {"strokes": [{"points": [{"x": 0.1, "y": 0.2}], "color": "#6259df", "size": 4}]}
        saved = self.guest_client.post(f"/api/meetings/{meeting['id']}/whiteboard/", {"state": state}, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.host_client.get(f"/api/meetings/{meeting['id']}/whiteboard/").data["state"], state)

    def test_scheduler_sends_one_reminder(self):
        meeting = self.create_meeting(scheduled_at=(timezone.now()+timedelta(minutes=5)).isoformat())
        call_command("run_notifications", "--once", verbosity=0)
        call_command("run_notifications", "--once", verbosity=0)
        self.assertEqual(Notification.objects.filter(meeting_id=meeting["id"], kind="meeting_reminder").count(), 1)

class MeetingWebSocketTests(TransactionTestCase):
    reset_sequences = True

    def test_authenticated_participant_can_exchange_chat_over_websocket(self):
        host = User.objects.create_user(username="wirehost", password="LongWireHostPassphrase!456")
        guest = User.objects.create_user(username="wireguest", password="LongWireGuestPassphrase!456")
        meeting = Meeting.objects.create(host=host, title="WebSocket check", status="live", started_at=timezone.now())
        MeetingParticipant.objects.create(meeting=meeting, user=host, role="host", state="active", joined_at=timezone.now())
        MeetingParticipant.objects.create(meeting=meeting, user=guest, state="active", joined_at=timezone.now())
        token = str(RefreshToken.for_user(guest).access_token)
        communicator = WebsocketCommunicator(application, f"/ws/meetings/{meeting.id}/", headers=[(b"origin", b"http://localhost:5173")], subprotocols=["connectx-auth", token])

        async def exchange():
            connected, protocol = await communicator.connect()
            self.assertTrue(connected)
            self.assertEqual(protocol, "connectx-auth")
            await communicator.receive_json_from()  # Initial participant presence event.
            await communicator.send_json_to({"event": "chat_message", "text": "Real-time hello"})
            event = await communicator.receive_json_from()
            self.assertEqual(event["event"], "chat_message")
            self.assertEqual(event["message"]["text"], "Real-time hello")
            await communicator.disconnect()

        async_to_sync(exchange)()
        self.assertTrue(MeetingMessage.objects.filter(meeting=meeting, text="Real-time hello").exists())

    def test_only_host_can_broadcast_live_translation_captions(self):
        host = User.objects.create_user(username="translationhost", password="LongTranslationHostPassphrase!456")
        guest = User.objects.create_user(username="translationguest", password="LongTranslationGuestPassphrase!456")
        meeting = Meeting.objects.create(host=host, title="Translation check", status="live", started_at=timezone.now(), translation_enabled=True, translation_source_language="ur-PK")
        MeetingParticipant.objects.create(meeting=meeting, user=host, role="host", state="active", joined_at=timezone.now())
        MeetingParticipant.objects.create(meeting=meeting, user=guest, state="active", joined_at=timezone.now())
        host_token = str(RefreshToken.for_user(host).access_token)
        guest_token = str(RefreshToken.for_user(guest).access_token)

        async def exchange():
            guest_socket = WebsocketCommunicator(application, f"/ws/meetings/{meeting.id}/", headers=[(b"origin", b"http://localhost:5173")], subprotocols=["connectx-auth", guest_token])
            connected, _ = await guest_socket.connect()
            self.assertTrue(connected)
            await guest_socket.receive_json_from()
            await guest_socket.send_json_to({
                "event": "translation_caption", "source_language": "ur-PK", "source_text": "سلام",
                "translations": {"en": "Hello"}, "final": True,
            })
            self.assertTrue(await guest_socket.receive_nothing(timeout=0.05))
            await guest_socket.disconnect()

            host_socket = WebsocketCommunicator(application, f"/ws/meetings/{meeting.id}/", headers=[(b"origin", b"http://localhost:5173")], subprotocols=["connectx-auth", host_token])
            connected, _ = await host_socket.connect()
            self.assertTrue(connected)
            await host_socket.receive_json_from()
            await host_socket.send_json_to({
                "event": "translation_caption", "caption_id": "caption-1", "source_language": "ur-PK",
                "source_text": "سلام", "translations": {"en": "Hello", "zh-Hans": "你好"}, "final": True,
            })
            caption = await host_socket.receive_json_from()
            self.assertEqual(caption["event"], "translation_caption")
            self.assertEqual(caption["translations"], {"en": "Hello", "zh-Hans": "你好"})
            await host_socket.disconnect()

        async_to_sync(exchange)()
