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

    def __init__(self, session: ClientSession, token: str | None = None) -> None:
        self._session = session
        self.token = token

    async def request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json", "Provider": PROVIDER, "X-Source": "dashboard"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
        return token

    async def validate(self) -> Any:
        return await self.request("GET", "/1/auth/whoami")

    async def refresh(self) -> str:
        data = await self.request("POST", "/1/auth/refresh")
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise EFluxAuthError("Session refresh failed")
        self.token = token
        return token
