from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

@database_sync_to_async
def user_for_token(raw):
    try:
        token = AccessToken(raw)
        return get_user_model().objects.get(pk=token["user_id"], is_active=True)
    except (TokenError, KeyError, get_user_model().DoesNotExist, ValueError):
        return AnonymousUser()

class JwtAuthMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        protocols = scope.get("subprotocols", [])
        raw = protocols[1] if len(protocols) > 1 and protocols[0] == "connectx-auth" else query.get("token", [""])[0]
        if raw and len(protocols) > 1 and protocols[0] == "connectx-auth":
            scope["connectx_subprotocol"] = "connectx-auth"
        scope["user"] = await user_for_token(raw) if raw else AnonymousUser()
        return await self.app(scope, receive, send)

