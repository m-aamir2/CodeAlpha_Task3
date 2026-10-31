import json
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from .models import MeetingMessage, MeetingParticipant, WhiteboardSnapshot
from .notifications import notify
from .serializers import user_data

@database_sync_to_async
def participant_for(meeting_id, user):
    return MeetingParticipant.objects.select_related("meeting", "user").filter(meeting_id=meeting_id, user=user, state="active").first()

@database_sync_to_async
def serialized_user(user):
    return user_data(user)

@database_sync_to_async
def persist_message(meeting_id, user, text):
    msg = MeetingMessage.objects.create(meeting_id=meeting_id, sender=user, text=text[:4000])
    for participant in MeetingParticipant.objects.select_related("meeting").filter(meeting_id=meeting_id, state="active").exclude(user_id=user.id):
        notify(participant.user, meeting=participant.meeting, kind="meeting_message", title=f"New message in {participant.meeting.title}", body=f"{user.get_full_name() or user.username}: {msg.text[:100]}")
    return {"id": msg.id, "text": msg.text, "created_at": msg.created_at.isoformat(), "sender": user_data(user)}

@database_sync_to_async
def persist_board(meeting_id, user, state):
    board, _ = WhiteboardSnapshot.objects.update_or_create(meeting_id=meeting_id, defaults={"state": state, "updated_by": user})
    return board.updated_at.isoformat()

class MeetingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.meeting_id = self.scope["url_route"]["kwargs"]["meeting_id"]
        self.user = self.scope.get("user")
        if not self.user or self.user.is_anonymous:
            await self.close(code=4401); return
        self.participant = await participant_for(self.meeting_id, self.user)
        if not self.participant:
            await self.close(code=4403); return
        self.group_name = f"meeting_{self.meeting_id.hex}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept(subprotocol=self.scope.get("connectx_subprotocol"))
        await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": {"event": "participant_online", "user": await serialized_user(self.user)}})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": {"event": "participant_offline", "user_id": self.user.id}})
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        event = content.get("event")
        if event == "chat_message":
            text = str(content.get("text", "")).strip()
            if not text or len(text) > 4000: return
            message = await persist_message(self.meeting_id, self.user, text)
            await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": {"event": "chat_message", "message": message}})
        elif event in ("whiteboard_draw", "whiteboard_clear", "whiteboard_undo"):
            payload = {"event": event, "user_id": self.user.id}
            if event == "whiteboard_draw":
                stroke = content.get("stroke")
                if not isinstance(stroke, dict) or len(json.dumps(stroke)) > 10000: return
                payload["stroke"] = stroke
            elif event == "whiteboard_clear":
                payload["state"] = content.get("state", {"strokes": []})
                await persist_board(self.meeting_id, self.user, payload["state"])
            await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": payload})
        elif event == "whiteboard_snapshot":
            state = content.get("state")
            if not isinstance(state, dict) or len(json.dumps(state)) > 1_000_000: return
            updated_at = await persist_board(self.meeting_id, self.user, state)
            await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": {"event": event, "state": state, "updated_at": updated_at, "user_id": self.user.id}})
        elif event == "participant_status":
            allowed = {"is_muted", "is_camera_off", "is_sharing_screen"}
            changed = {k: bool(v) for k, v in content.get("status", {}).items() if k in allowed}
            if changed:
                await self._save_status(changed)
                await self.channel_layer.group_send(self.group_name, {"type": "meeting.event", "payload": {"event": event, "user_id": self.user.id, "status": changed}})

    @database_sync_to_async
    def _save_status(self, changed):
        MeetingParticipant.objects.filter(pk=self.participant.pk).update(**changed)

    async def meeting_event(self, event):
        await self.send_json(event["payload"])

class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or self.user.is_anonymous:
            await self.close(code=4401); return
        self.group_name = f"user_{self.user.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept(subprotocol=self.scope.get("connectx_subprotocol"))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notification_event(self, event):
        await self.send_json(event["payload"])

