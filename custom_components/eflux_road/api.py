"""E-Flux session API. Never log credentials, token IDs or raw responses."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import API_URL, PROVIDER


class EFluxError(Exception):
    """An E-Flux request failed."""


class EFluxAuthError(EFluxError):
    """The session cannot be used."""


class EFluxVerificationRequired(EFluxAuthError):
    """Human verification or additional authentication is required."""


class EFluxMfaRequired(EFluxVerificationRequired):
    """An existing second factor must be verified before a session is issued."""

    def __init__(self, pending_id: str, methods: tuple[str, ...]) -> None:
        super().__init__("E-Flux requires a second factor")
        self.pending_id = pending_id
        self.methods = methods


class EFluxRequestError(EFluxError):
    """A request failed with an HTTP status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"E-Flux returned HTTP {status}")
        self.status = status


class EFluxClient:
    """Minimal client with explicit request boundaries."""

    def __init__(self, session: ClientSession, token: str | None = None) -> None:
        self._session = session
        self.token = token
        self.refresh_requested = False

    async def request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json", "Provider": PROVIDER, "X-Source": "dashboard"}
        request_token = self.token if auth else None
        if request_token:
            headers["Authorization"] = f"Bearer {request_token}"
        try:
            async with asyncio.timeout(15):
                async with self._session.request(
                    method, API_URL + path, headers=headers, json=payload,
                    allow_redirects=False,
                ) as response:
                    status = response.status
                    if status == 403:
                        if "json" not in response.headers.get("Content-Type", "").lower():
                            raise EFluxVerificationRequired("E-Flux requires browser verification")
                        try:
                            error_data = await response.json()
                        except (ValueError, ClientError):
                            error_data = None
                        if isinstance(error_data, dict) and error_data.get("cloudflare_error") is True:
                            raise EFluxVerificationRequired("E-Flux blocked this client")
                    if status in (401, 403):
                        raise EFluxAuthError("E-Flux authentication failed")
                    if status >= 400:
                        raise EFluxRequestError(status)
                    try:
                        data = await response.json()
                    except (ValueError, ClientError) as err:
                        raise EFluxError("Invalid E-Flux response") from err
                    if not isinstance(data, dict) or "data" not in data:
                        raise EFluxError("Unexpected E-Flux response")
                    # Do not retry a request here: a start or stop may have succeeded.
                    # Ignore hints from anonymous requests and requests made with an old token.
                    if (request_token and self.token == request_token and path != "/1/auth/refresh"
                            and response.headers.get("X-Token-Status", "").lower() == "refresh"):
                        self.refresh_requested = True
                    return data["data"]
        except (ClientError, TimeoutError) as err:
            raise EFluxError("E-Flux request failed") from err

    async def login(self, username: str, password: str) -> str:
        data = await self.request(
            "POST", "/1/auth/login",
            payload={"email": username, "password": password}, auth=False,
        )
        if not isinstance(data, dict):
            raise EFluxAuthError("Unexpected login response")
        if data.get("mfaRequired"):
            mfa = data.get("mfa")
            if isinstance(mfa, dict):
                pending_id, methods = mfa.get("pendingId"), mfa.get("methods")
                if isinstance(pending_id, str) and pending_id and isinstance(methods, list):
                    supported = tuple(method for method in ("totp", "sms") if method in methods)
                    if supported:
                        raise EFluxMfaRequired(pending_id, supported)
            raise EFluxVerificationRequired("E-Flux requires browser verification")
        if data.get("mfaEnrolment") or data.get("mfaVerifyEmail"):
            raise EFluxVerificationRequired("E-Flux requires additional verification")
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("No session returned")
        self.token = token
        return token

    async def validate(self) -> Any:
        return await self.request("GET", "/1/auth/whoami")

    async def send_sms_code(self, pending_id: str) -> None:
        """Request an SMS only when the user chooses that method."""
        await self.request("POST", "/1/auth/mfa/challenge",
                           payload={"pendingId": pending_id, "method": "sms"}, auth=False)

    async def verify_mfa(self, pending_id: str, method: str, code: str) -> str:
        """Complete a pending login without storing the one-time code."""
        if method not in ("totp", "sms"):
            raise EFluxVerificationRequired("E-Flux requires browser verification")
        data = await self.request("POST", f"/1/auth/mfa/{method}/verify",
                                  payload={"pendingId": pending_id, "code": code}, auth=False)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("No session returned")
        self.token = token
        return token

    async def refresh(self) -> str:
        data = await self.request("POST", "/1/auth/refresh")
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("Session refresh failed")
        self.token = token
        self.refresh_requested = False
        return token
