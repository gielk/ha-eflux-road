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


class EFluxRequestError(EFluxError):
    """A request failed with an HTTP status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"E-Flux returned HTTP {status}")
        self.status = status


class EFluxClient:
    """Minimal client with explicit request boundaries."""

    def __init__(
        self,
        session: ClientSession,
        token: str | None = None,
        device_token: str | None = None,
    ) -> None:
        self._session = session
        self.token = token
        # Road's dashboard pairs its bearer session with this rotating device
        # credential. It is deliberately kept separate from the JWT because
        # both values rotate together after a successful refresh.
        self.device_token = device_token
        self.refresh_requested = False

    async def request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None,
        auth: bool = True, json_content_type: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json", "Provider": PROVIDER, "X-Source": "dashboard"}
        request_token = self.token if auth else None
        if request_token:
            headers["Authorization"] = f"Bearer {request_token}"
        if isinstance(self.device_token, str) and self.device_token:
            headers["X-Device-Id"] = self.device_token
        if json_content_type:
            # The dashboard sends this header for its otherwise empty refresh
            # request. Keep the wire format compatible without logging either
            # credential or a response body.
            headers["Content-Type"] = "application/json"
        try:
            async with asyncio.timeout(15):
                async with self._session.request(
                    method, API_URL + path, headers=headers, json=payload,
                    allow_redirects=False,
                ) as response:
                    status = response.status
                    if status == 403 and "json" not in response.headers.get("Content-Type", ""):
                        raise EFluxVerificationRequired("E-Flux requires browser verification")
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
                    # A late response for an old token must not make a newer
                    # session refresh again. Do not react to the refresh
                    # endpoint itself either.
                    if (request_token and request_token == self.token
                            and path != "/1/auth/refresh"
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
        if data.get("mfaRequired") or data.get("mfaEnrolment") or data.get("mfaVerifyEmail"):
            raise EFluxVerificationRequired("E-Flux requires additional verification")
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("No session returned")
        self.token = token
        device_token = data.get("deviceToken")
        if isinstance(device_token, str) and device_token:
            self.device_token = device_token
        return token

    async def validate(self) -> Any:
        return await self.request("GET", "/1/auth/whoami")

    async def refresh(self) -> tuple[str, str]:
        """Rotate the paired browser-session and device credentials."""
        if not self.token or not isinstance(self.device_token, str) or not self.device_token:
            raise EFluxAuthError("E-Flux session renewal needs a device credential")
        data = await self.request(
            "POST", "/1/auth/refresh", json_content_type=True,
        )
        if not isinstance(data, dict):
            raise EFluxAuthError("Unexpected session renewal response")
        token = data.get("token")
        rotated_device = data.get("deviceToken", self.device_token)
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("E-Flux did not return a renewed session")
        if not isinstance(rotated_device, str) or not rotated_device:
            raise EFluxAuthError("E-Flux did not return a usable device credential")
        self.token = token
        self.device_token = rotated_device
        self.refresh_requested = False
        return token, rotated_device
