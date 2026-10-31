import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Notification

logger = logging.getLogger(__name__)

def notify(recipient, *, kind, title, body="", meeting=None):
    notice = Notification.objects.create(recipient=recipient, meeting=meeting, kind=kind, title=title, body=body[:500])
    if notice.meeting_id:
        meeting_code = notice.meeting.code
    else:
        meeting_code = ""
    layer = get_channel_layer()
    try:
        async_to_sync(layer.group_send)(f"user_{recipient.pk}", {"type": "notification.event", "payload": {
            "id": notice.pk, "kind": notice.kind, "title": notice.title, "body": notice.body,
            "meeting": str(notice.meeting_id) if notice.meeting_id else None,
            "meeting_code": meeting_code, "read_at": None, "created_at": notice.created_at.isoformat(),
        }})
    except Exception:
        logger.exception("Could not broadcast ConnectX notification to user %s", recipient.pk)
    return notice

