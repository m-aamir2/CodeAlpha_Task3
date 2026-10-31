from datetime import timedelta
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
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

    def test_registration_and_email_login(self):
        response = self.client.post("/api/auth/register/", {"name": "Taylor Reed", "username": "taylor", "email": "TAYLOR@example.test", "password": "Taylor-Long-Unique-348!"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)
        self.assertTrue(User.objects.get(username="taylor").check_password("Taylor-Long-Unique-348!"))
        login = self.client.post("/api/auth/login/", {"username": "taylor@example.test", "password": "Taylor-Long-Unique-348!"}, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(login.data["user"]["username"], "taylor")

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
