from django.urls import path
from .consumers import MeetingConsumer, NotificationConsumer

websocket_urlpatterns = [path("ws/meetings/<uuid:meeting_id>/", MeetingConsumer.as_asgi()), path("ws/notifications/", NotificationConsumer.as_asgi())]

