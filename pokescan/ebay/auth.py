from __future__ import annotations
import asyncio, base64, time
import httpx
from .errors import AuthError

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayAuth:
    """Client-credentials (application) token, cached and refreshed ~5 min before expiry."""

    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient):
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._http = http
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            resp = await self._http.post(
                TOKEN_URL,
                headers={"Authorization": f"Basic {self._basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": SCOPE},
            )
            if resp.status_code != 200:
                raise AuthError(f"Token request failed: {resp.status_code} {resp.text[:200]}")
            body = resp.json()
            self._token = body["access_token"]
            self._expires_at = time.time() + int(body.get("expires_in", 7200))
            return self._token

    def invalidate(self) -> None:
        self._token = None
