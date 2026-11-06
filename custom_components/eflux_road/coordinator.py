"""Coordinator and safe start handling for an E-Flux charger."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EFluxAuthError, EFluxClient, EFluxError, EFluxRequestError
from .const import (
    CONF_CARD_ID, CONF_CONNECTOR_ID, CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID,
    CONF_SESSION_TOKEN, DOMAIN,
)

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED"}
ACTIVE = ["ACTIVE", "PENDING", "CONGESTED", "IDLING", "RESERVATION"]


def objects(value: Any, depth: int = 0) -> list[dict]:
    """Find nested response objects without exposing their contents."""
    if depth > 8:
        return []
    if isinstance(value, list):
        return [found for child in value for found in objects(child, depth + 1)]
    if isinstance(value, dict):
        return [value] + [found for child in value.values() for found in objects(child, depth + 1)]
    return []


def find_card(data: Any, identifier: str) -> dict:
    """Require one active, internally consistent card and token."""
    matches = [
        item for item in objects(data)
        if isinstance(item.get("tokenId"), str)
        and isinstance(item.get("token"), dict)
        and item["token"].get("id") == item["tokenId"]
        and str(item.get("status", "")).upper() == "ACTIVE"
        and any(
            identifier in (child.get("customId"), child.get("visualNumber"), child.get("cardNumber"))
            for child in objects(item)
        )
    ]
    if len(matches) != 1:
        raise EFluxError("The configured active charge card was not uniquely identified")
    return matches[0]


def status(item: dict) -> str:
    """Get a normalized status."""
    return str(item.get("status", "")).upper()


def jwt_expiry(token: str) -> float:
    """Read a session's expiration without exposing its contents."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except (ValueError, IndexError, TypeError):
        return 0


class EFluxCoordinator(DataUpdateCoordinator):
    """Track E-Flux status and serialize session start requests."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EFluxClient) -> None:
        super().__init__(
            hass, logger=logging.getLogger(__name__),
            name="E-Flux by Road", update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        self.client = client
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self.last_start = "Geen start aangevraagd"

    async def ensure_session(self) -> None:
        """Refresh an expiring session and persist the rotated token."""
        if not self.client.token:
            raise EFluxAuthError("Missing session")
        expiry = jwt_expiry(self.client.token)
        if not expiry:
            raise EFluxAuthError("Invalid session")
        if expiry - time.time() < 900:
            async with self._lock:
                if jwt_expiry(self.client.token) - time.time() < 900:
                    new_token = await self.client.refresh()
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={**self.entry.data, CONF_SESSION_TOKEN: new_token},
                    )

    async def _async_update_data(self) -> dict:
        try:
            await self.ensure_session()
            active, remote, locations = await asyncio.gather(
                self.client.request("POST", "/1/active-sessions/mine",
                                    payload={"limit": 50, "statuses": ACTIVE}),
                self.client.request("GET", "/1/remote-sessions/msp/mine"),
                self.client.request("POST", "/1/map/locations",
                                    payload={"ids": [self.entry.data[CONF_LOCATION_ID]]}, auth=False),
            )
            if not all(isinstance(value, list) for value in (active, remote, locations)):
                raise EFluxError("Unexpected session or location format")
            location = next((item for item in locations
                             if item.get("id") == self.entry.data[CONF_LOCATION_ID]), None)
            evse = next((item for item in (location or {}).get("evses", [])
                         if item.get("uid") == self.entry.data[CONF_EVSE_UID]
                         and item.get("evse_id") == self.entry.data[CONF_EVSE_ID]), None)
            if not evse:
                raise EFluxError("The configured charging station was not found")
            latest = max(remote, key=lambda item: item.get("createdAt") or "", default={})
            pending_since = self.entry.data.get("pending_since")
            if pending_since:
                if len(active) > 0 or status(latest) in {"ACTIVE", "STARTED", "COMPLETED"}:
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={key: value for key, value in self.entry.data.items()
                                          if key != "pending_since"},
                    )
                elif status(latest) in TERMINAL:
                    try:
                        created = datetime.fromisoformat(latest["createdAt"].replace("Z", "+00:00")).timestamp()
                    except (KeyError, TypeError, ValueError):
                        created = 0
                    same_connector = (
                        str(latest.get("connectorId")) == self.entry.data[CONF_CONNECTOR_ID]
                        and latest.get("evseId") in (
                            self.entry.data[CONF_EVSE_ID], self.entry.data[CONF_EVSE_UID]
                        )
                    )
                    if same_connector and created >= pending_since - 60:
                        self.hass.config_entries.async_update_entry(
                            self.entry, data={key: value for key, value in self.entry.data.items()
                                              if key != "pending_since"},
                        )
            return {
                "connector": status(evse) or "UNKNOWN",
                "active": len(active),
                "remote": status(latest) or "UNKNOWN",
                "remote_reason": latest.get("cancelledReason"),
                "last_start": self.last_start,
            }
        except EFluxAuthError as err:
            raise ConfigEntryAuthFailed from err
        except EFluxError as err:
            raise UpdateFailed(str(err)) from err

    async def start_once(self) -> None:
        """Request exactly one start. Never repeat after an uncertain outcome."""
        await self.ensure_session()
        async with self._lock:
            pending_since = self.entry.data.get("pending_since")
            if pending_since:
                if time.time() - float(pending_since) > 180:
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={k: v for k, v in self.entry.data.items() if k != "pending_since"}
                    )
                else:
                    raise HomeAssistantError("A previous start must be resolved before retrying")
            try:
                cards, active, history, locations = await asyncio.gather(
                    self.client.request("GET", "/1/cards/mine"),
                    self.client.request("POST", "/1/active-sessions/mine",
                                        payload={"limit": 50, "statuses": ACTIVE}),
                    self.client.request("GET", "/1/remote-sessions/msp/mine"),
                    self.client.request("POST", "/1/map/locations",
                                        payload={"ids": [self.entry.data[CONF_LOCATION_ID]]}, auth=False),
                )
                if not all(isinstance(value, list) for value in (active, history, locations)):
                    raise EFluxError("Cannot verify session state")

                def is_recent_pending(item: dict) -> bool:
                    if status(item) in TERMINAL:
                        return False
                    try:
                        created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00")).timestamp()
                        return (time.time() - created) < 300
                    except (KeyError, TypeError, ValueError):
                        return True

                if active or any(is_recent_pending(item) for item in history):
                    raise EFluxError("A session or start request is already active")
                card = find_card(cards, self.entry.data[CONF_CARD_ID])
                location = next((item for item in locations
                                 if item.get("id") == self.entry.data[CONF_LOCATION_ID]), None)
                evse = next((item for item in (location or {}).get("evses", [])
                             if item.get("uid") == self.entry.data[CONF_EVSE_UID]
                             and item.get("evse_id") == self.entry.data[CONF_EVSE_ID]), None)
                if not evse or not any(str(c.get("id")) == self.entry.data[CONF_CONNECTOR_ID]
                                       for c in evse.get("connectors", [])):
                    raise EFluxError("Configured connector not found")
                if "REMOTE_START_STOP_CAPABLE" not in evse.get("capabilities", []):
                    raise EFluxError("Connector does not support remote starts")
                if status(evse) not in {"AVAILABLE", "PREPARING", "CHARGING", "OCCUPIED"}:
                    raise EFluxError("Connector is not ready")
                self.last_start = "Startverzoek verzonden"
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, "pending_since": time.time()},
                )
                await self.client.request("POST", "/1/remote-sessions/msp", payload={
                    "connectorId": self.entry.data[CONF_CONNECTOR_ID],
                    "evseUid": self.entry.data[CONF_EVSE_UID],
                    "locationId": self.entry.data[CONF_LOCATION_ID],
                    "paymentType": "TOKEN",
                    "tokenId": card["tokenId"],
                })
                self.last_start = "Startverzoek geaccepteerd"
            except EFluxRequestError as err:
                # A 500 can still create a remote-session record; do not resend.
                self.last_start = f"Uitkomst controleren (HTTP {err.status})"
                raise HomeAssistantError(self.last_start) from err
            except EFluxError as err:
                self.last_start = str(err)
                raise HomeAssistantError(self.last_start) from err
            finally:
                self.async_set_updated_data({**(self.data or {}), "last_start": self.last_start})

    async def start_until_confirmed(self) -> None:
        """Retry only after Road confirms the previous attempt is terminal."""
        if self._start_lock.locked():
            raise HomeAssistantError("A start request is already being processed")
        async with self._start_lock:
            await self._start_with_retries()

    async def _start_with_retries(self) -> None:
        """Run a bounded sequence of individually verified start requests."""
        deadline = time.monotonic() + 150
        for attempt in range(3):
            try:
                await self.start_once()
            except HomeAssistantError:
                if not self.entry.data.get("pending_since"):
                    raise
            while time.monotonic() < deadline and self.entry.data.get("pending_since"):
                await asyncio.sleep(5)
                await self.async_request_refresh()
                if not self.last_update_success:
                    raise HomeAssistantError("The start result could not be checked safely")
                if (self.data or {}).get("active", 0) > 0:
                    self.last_start = "E-Flux meldt een actieve sessie"
                    if self.entry.data.get("pending_since"):
                        self.hass.config_entries.async_update_entry(
                            self.entry, data={key: value for key, value in self.entry.data.items()
                                              if key != "pending_since"},
                        )
                    self.async_set_updated_data({**self.data, "last_start": self.last_start})
                    return
            if self.entry.data.get("pending_since"):
                self.hass.config_entries.async_update_entry(
                    self.entry, data={key: value for key, value in self.entry.data.items()
                                      if key != "pending_since"},
                )
                raise HomeAssistantError("The start outcome is unknown; retry is blocked")
            if attempt < 2 and time.monotonic() < deadline:
                await asyncio.sleep(15)
            else:
                break
        raise HomeAssistantError("The charging session did not become active")
