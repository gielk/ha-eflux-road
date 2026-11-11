"""Coordinator and safe start handling for an E-Flux charger."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EFluxAuthError, EFluxClient, EFluxError, EFluxRequestError
from .const import (
    CONF_CARD_ID, CONF_CONNECTOR_ID, CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID,
    CONF_DEVICE_TOKEN, CONF_SESSION_TOKEN, DOMAIN,
)

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED"}
ACTIVE = ["ACTIVE", "PENDING", "CONGESTED", "IDLING", "RESERVATION"]
FAILURE = {"CANCELLED", "FAILED", "REJECTED", "EXPIRED"}
SUCCESS = {"ACTIVE", "STARTED", "COMPLETED"}
# A start request without any new record at Road after this many seconds
# never reached the charger. It must not block later starts forever.
PENDING_TIMEOUT = 120


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


def terminal(item: dict) -> bool:
    """A record is finished when Road says so, or when its own transaction is.

    Road sets ERROR on a remote request after a stop on an already finished
    transaction. Without a live embedded session that request is over.
    """
    if status(item) in TERMINAL:
        return True
    if status(item) != "ERROR":
        return False
    session = item.get("activeSession")
    return not isinstance(session, dict) or status(session) in TERMINAL


def unresolved_sessions(items: list[dict], remote: bool = False) -> list[dict]:
    """Filter locally: Road may return completed records despite its filter.

    Unknown/missing statuses remain blocking rather than being treated as safe.
    Only start requests (remote) may count a closed ERROR record as finished;
    a charging session with any non-terminal status always blocks.
    """
    if not all(isinstance(item, dict) for item in items):
        raise EFluxError("Unexpected session record")
    if remote:
        return [item for item in items if not terminal(item)]
    return [item for item in items if status(item) not in TERMINAL]


def new_requests(remote: list, baseline: Any, config: dict) -> list[dict] | None:
    """Start records for this connector that were not known before a start.

    None means the baseline is missing, so nothing can be concluded.
    """
    if not isinstance(baseline, list):
        return None
    return [
        item for item in remote
        if isinstance(item, dict) and item.get("id")
        and str(item["id"]) not in baseline
        and str(item.get("connectorId")) == config[CONF_CONNECTOR_ID]
        and item.get("evseId") in (config[CONF_EVSE_ID], config[CONF_EVSE_UID])
    ]


def remote_for_connector(item: dict, config: dict) -> bool:
    """Only match an explicitly identified connector and EVSE."""
    return (str(item.get("connectorId")) == config[CONF_CONNECTOR_ID]
            and item.get("evseId") in (config[CONF_EVSE_ID], config[CONF_EVSE_UID])
            and (not item.get("locationId") or item["locationId"] == config[CONF_LOCATION_ID]))


def remote_has_completed_session(item: dict, config: dict) -> bool:
    """Recognise a stale ACTIVE request only with its own completed transaction.

    Road can retain ACTIVE on a remote request after the embedded transaction
    becomes COMPLETED. Require both session IDs and the selected connector;
    timestamps, zero watts and an empty account overview alone are not proof.
    """
    session = item.get("activeSession")
    session_id = item.get("activeSessionId")
    return (
        status(item) == "ACTIVE"
        and remote_for_connector(item, config)
        and isinstance(session, dict)
        and status(session) == "COMPLETED"
        and isinstance(session_id, str)
        and bool(session_id)
        and session.get("id") == session_id
    )


def selected_live_session(remote: list[dict], active: list[dict], config: dict):
    """Use only a unique remote session at this connector and its linked telemetry."""
    matched = [item for item in remote if isinstance(item, dict)
               and status(item) == "ACTIVE" and item.get("id") and remote_for_connector(item, config)
               and not remote_has_completed_session(item, config)]
    if len(matched) != 1:
        return None, None
    remote_item = matched[0]
    embedded = remote_item.get("activeSession")
    linked = [item for item in active if item.get("remoteSessionId") == remote_item["id"]]
    if isinstance(embedded, dict):
        return remote_item, embedded
    return remote_item, linked[0] if len(linked) == 1 else None


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
        self.config = {**entry.data, **entry.options}
        self.client = client
        self._lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self.last_start = "Geen start aangevraagd"
        self._last_resolution = None

    async def ensure_session(self) -> None:
        """Refresh a paired session before it can affect an automation."""
        if not self.client.token:
            raise EFluxAuthError("Missing session")
        expiry = jwt_expiry(self.client.token)
        if not expiry:
            raise EFluxAuthError("Invalid session")
        refresh_needed = (
            getattr(self.client, "refresh_requested", False)
            or expiry - time.time() < 900
        )
        if not refresh_needed:
            return
        if not getattr(self.client, "device_token", None):
            raise EFluxAuthError("E-Flux-sessie moet opnieuw worden gekoppeld")
        # A polling update and a manual start can arrive together. Exactly one
        # caller may rotate the pair, and the complete new pair is stored in
        # the config entry only after Road accepted it.
        async with self._refresh_lock:
            current_expiry = jwt_expiry(self.client.token)
            if not current_expiry:
                raise EFluxAuthError("Invalid renewed session")
            if (not getattr(self.client, "refresh_requested", False)
                    and current_expiry - time.time() >= 900):
                return
            token, device_token = await self.client.refresh()
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_SESSION_TOKEN: token,
                    CONF_DEVICE_TOKEN: device_token,
                },
            )

    async def _async_update_data(self) -> dict:
        try:
            await self.ensure_session()
            active, remote, locations = await asyncio.gather(
                self.client.request("POST", "/1/active-sessions/mine",
                                    payload={"limit": 50, "statuses": ACTIVE}),
                self.client.request("GET", "/1/remote-sessions/msp/mine"),
                self.client.request("POST", "/1/map/locations",
                                    payload={"ids": [self.config[CONF_LOCATION_ID]]}, auth=False),
            )
            await self.ensure_session()
            if not all(isinstance(value, list) for value in (active, remote, locations)):
                raise EFluxError("Unexpected session or location format")
            if len(active) >= 50:
                raise EFluxError("Session overview may be incomplete")
            active = unresolved_sessions(active)
            location = next((item for item in locations
                             if item.get("id") == self.config[CONF_LOCATION_ID]), None)
            evse = next((item for item in (location or {}).get("evses", [])
                         if item.get("uid") == self.config[CONF_EVSE_UID]
                         and item.get("evse_id") == self.config[CONF_EVSE_ID]), None)
            if not evse:
                raise EFluxError("The configured charging station was not found")
            try:
                card_data = await self.client.request("GET", "/1/cards/mine")
                matched_cards = [item for item in objects(card_data)
                                 if isinstance(item.get("token"), dict)
                                 and self.config[CONF_CARD_ID] in (
                                     item["token"].get("customId"), item["token"].get("visualNumber"),
                                     item["token"].get("cardNumber"))]
                card_status = status(matched_cards[0]) if len(matched_cards) == 1 else None
            except EFluxError:
                card_status = None
            if not all(isinstance(item, dict) for item in remote):
                raise EFluxError("Unexpected remote session record")
            latest = max(remote, key=lambda item: item.get("createdAt") or "", default={})
            selected = [item for item in remote if remote_for_connector(item, self.config)]
            latest_selected = max(selected, key=lambda item: item.get("createdAt") or "", default={})
            selected_raw_status = status(latest_selected) or "UNKNOWN"
            selected_status = (
                "COMPLETED" if not active and latest_selected and (
                    remote_has_completed_session(latest_selected, self.config)
                    or (selected_raw_status == "ERROR" and terminal(latest_selected)))
                else selected_raw_status
            )
            remote_session, live = selected_live_session(remote, active, self.config)
            pending_stop = self.entry.data.get("pending_stop")
            stopped = next((item for item in remote if item.get("id") == pending_stop), None)
            if pending_stop and stopped and (
                terminal(stopped)
                or (not active and remote_has_completed_session(stopped, self.config))
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry, data={k: v for k, v in self.entry.data.items() if k != "pending_stop"})
            pending_since = self.entry.data.get("pending_since")
            if pending_since:
                # A record must be new, uniquely identifiable and for this
                # connector. A timestamp alone cannot distinguish old requests.
                candidates = new_requests(
                    remote, self.entry.data.get("pending_known_ids"), self.config)
                if candidates and len(candidates) == 1:
                    candidate = candidates[0]
                    try:
                        created = datetime.fromisoformat(candidate["createdAt"].replace("Z", "+00:00")).timestamp()
                    except (KeyError, TypeError, ValueError):
                        created = 0
                    candidate_status = status(candidate)
                    if candidate_status == "ERROR":
                        # With a session its status decides. Without one the
                        # request is over, but never a confirmed refusal.
                        session = candidate.get("activeSession")
                        candidate_status = status(session) if isinstance(session, dict) else "ERROR"
                    if created >= pending_since - 60 and (
                            candidate_status in FAILURE | SUCCESS
                            or (candidate_status == "ERROR" and terminal(candidate))):
                        self._last_resolution = candidate_status
                        # Keep the text in step with the outcome, also when the
                        # caller was cancelled and can no longer report it.
                        self.last_start = (
                            "E-Flux bevestigt het startverzoek" if candidate_status in SUCCESS
                            else f"Start niet gelukt volgens E-Flux ({candidate_status})")
                        self._clear_pending()
                elif (candidates == [] and not active
                      and time.time() - pending_since > PENDING_TIMEOUT):
                    # Road made no record for this connector, so the request
                    # never reached the charger. Unblock later starts.
                    self._last_resolution = "NO_RECORD"
                    self.last_start = "Geen sessie aangemaakt door E-Flux"
                    self.logger.warning("E-Flux startverzoek vervallen: geen nieuw record na %s s",
                                        PENDING_TIMEOUT)
                    self._clear_pending()
            return {
                "connector": status(evse) or "UNKNOWN",
                "active": len(active),
                "remote": status(latest) or "UNKNOWN",
                "remote_connector": selected_status,
                "remote_connector_raw": selected_raw_status,
                "last_successful_update": datetime.now(timezone.utc).isoformat(),
                "remote_reason": latest.get("cancelledReason"),
                "last_start": self.last_start,
                "session_energy": (live or {}).get("kwh"),
                "session_started": (live or {}).get("startedAt"),
                "charging_state": (live or {}).get("chargingState"),
                "card": "••••" + self.config[CONF_CARD_ID][-4:],
                "card_status": card_status,
                "max_power": evse.get("maxPower"),
                "location_name": location.get("name"),
                "location_address": location.get("address"),
                "location_city": location.get("city"),
            }
        except EFluxAuthError as err:
            raise ConfigEntryAuthFailed from err
        except EFluxError as err:
            raise UpdateFailed(str(err)) from err

    def _clear_pending(self) -> None:
        """Forget a start request once its outcome is known."""
        self.hass.config_entries.async_update_entry(
            self.entry, data={key: value for key, value in self.entry.data.items()
                              if key not in {"pending_since", "pending_known_ids"}},
        )

    async def _rejected_without_record(self) -> bool:
        """After a 4xx, unblock only when Road shows no new record."""
        if not self.entry.data.get("pending_since"):
            return True
        try:
            remote = await self.client.request("GET", "/1/remote-sessions/msp/mine")
        except EFluxError:
            return False
        if not isinstance(remote, list):
            return False
        if new_requests(remote, self.entry.data.get("pending_known_ids"), self.config) != []:
            return False
        self._clear_pending()
        return True

    async def _close_stale(self, stale: list[dict], history: list) -> list:
        """Stop remote requests whose own transaction already completed.

        Road leaves such a request ACTIVE, seen after a session without
        delivered energy, and refuses a new start meanwhile (HTTP 412). A stop
        on it answers HTTP 500 but moves the request out of ACTIVE.
        """
        stale_ids = {str(item["id"]) for item in stale}
        for item in stale:
            try:
                await self.client.request(
                    "POST", f"/1/remote-sessions/msp/{quote(str(item['id']), safe='')}/stop")
            except EFluxRequestError:
                pass
        self.logger.info("E-Flux: %s verweesd startverzoek afgesloten voor nieuwe start", len(stale))
        for _ in range(4):
            await asyncio.sleep(2.5)
            refreshed = await self.client.request("GET", "/1/remote-sessions/msp/mine")
            if not isinstance(refreshed, list) or not all(
                    isinstance(item, dict) and item.get("id") for item in refreshed):
                raise EFluxError("Cannot identify existing start requests")
            history = refreshed
            if not any(str(item["id"]) in stale_ids and status(item) == "ACTIVE"
                       for item in history):
                break
        return history

    async def start_once(self) -> None:
        """Request exactly one start. Never repeat after an uncertain outcome."""
        await self.ensure_session()
        async with self._lock:
            pending_since = self.entry.data.get("pending_since")
            if pending_since or self.entry.data.get("pending_stop"):
                raise HomeAssistantError("A previous start must be resolved before retrying")
            sent = False
            try:
                cards, active, history, locations = await asyncio.gather(
                    self.client.request("GET", "/1/cards/mine"),
                    self.client.request("POST", "/1/active-sessions/mine",
                                        payload={"limit": 50, "statuses": ACTIVE}),
                    self.client.request("GET", "/1/remote-sessions/msp/mine"),
                    self.client.request("POST", "/1/map/locations",
                                        payload={"ids": [self.config[CONF_LOCATION_ID]]}, auth=False),
                )
                await self.ensure_session()
                if not all(isinstance(value, list) for value in (active, history, locations)):
                    raise EFluxError("Cannot verify session state")
                if len(active) >= 50:
                    raise EFluxError("Session overview may be incomplete")
                if not all(isinstance(item, dict) and item.get("id") for item in history):
                    raise EFluxError("Cannot identify existing start requests")
                unresolved_history = [item for item in unresolved_sessions(history, remote=True)
                                      if not remote_has_completed_session(item, self.config)]
                if unresolved_sessions(active) or unresolved_history:
                    raise EFluxError("A session or start request is already active")
                card = find_card(cards, self.config[CONF_CARD_ID])
                location = next((item for item in locations
                                 if item.get("id") == self.config[CONF_LOCATION_ID]), None)
                evse = next((item for item in (location or {}).get("evses", [])
                             if item.get("uid") == self.config[CONF_EVSE_UID]
                             and item.get("evse_id") == self.config[CONF_EVSE_ID]), None)
                if not evse or not any(str(c.get("id")) == self.config[CONF_CONNECTOR_ID]
                                       for c in evse.get("connectors", [])):
                    raise EFluxError("Configured connector not found")
                if "REMOTE_START_STOP_CAPABLE" not in evse.get("capabilities", []):
                    raise EFluxError("Connector does not support remote starts")
                if status(evse) not in {"AVAILABLE", "PREPARING", "CHARGING", "OCCUPIED"}:
                    raise EFluxError("Connector is not ready")
                stale = [item for item in history
                         if remote_has_completed_session(item, self.config)
                         and not item.get("stopRequestedAt")]
                if stale:
                    history = await self._close_stale(stale, history)
                self.last_start = "Startverzoek verzonden"
                self._last_resolution = None
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, "pending_since": time.time(),
                                      "pending_known_ids": [str(item["id"]) for item in history]},
                )
                sent = True
                await self.client.request("POST", "/1/remote-sessions/msp", payload={
                    "connectorId": self.config[CONF_CONNECTOR_ID],
                    "evseUid": self.config[CONF_EVSE_UID],
                    "locationId": self.config[CONF_LOCATION_ID],
                    "paymentType": "TOKEN",
                    "tokenId": card["tokenId"],
                })
                self.last_start = "Startverzoek geaccepteerd"
            except EFluxRequestError as err:
                # Only the status code is kept: Road's error text stays private.
                if not sent:
                    self.last_start = f"E-Flux controle mislukt (HTTP {err.status})"
                elif 400 <= err.status < 500 and await self._rejected_without_record():
                    # Road refused outright and made no record: nothing started.
                    self._last_resolution = "REJECTED"
                    self.last_start = f"Geweigerd door E-Flux (HTTP {err.status})"
                else:
                    # A 500 can still create a remote-session record; do not resend.
                    self.last_start = f"Uitkomst controleren (HTTP {err.status})"
                self.logger.warning("E-Flux startverzoek: %s", self.last_start)
                raise HomeAssistantError(self.last_start) from err
            except EFluxError as err:
                if sent and isinstance(err, EFluxAuthError):
                    # Road refused the request itself, so nothing was started.
                    # A lasting block would also keep the session from being re-linked.
                    self._last_resolution = "REJECTED"
                    self._clear_pending()
                self.last_start = str(err)
                raise HomeAssistantError(self.last_start) from err
            finally:
                self.async_set_updated_data({**(self.data or {}), "last_start": self.last_start})

    async def stop_once(self) -> None:
        """Stop only one freshly identified active remote session; never retry blindly."""
        await self.ensure_session()
        async with self._lock:
            if self.entry.data.get("pending_stop"):
                raise HomeAssistantError("A previous stop request has not been resolved")
            if self.entry.data.get("pending_since") or self._start_lock.locked():
                raise HomeAssistantError("A start request is still being processed")
            try:
                remote = await self.client.request("GET", "/1/remote-sessions/msp/mine")
                await self.ensure_session()
                if not isinstance(remote, list):
                    raise EFluxError("Cannot verify the current remote session")
                selected, _ = selected_live_session(remote, [], self.config)
                if not selected or selected.get("stopRequestedAt"):
                    raise EFluxError("No unique active remote session can be stopped")
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, "pending_stop": str(selected["id"])})
                await self.client.request("POST", f"/1/remote-sessions/msp/{quote(str(selected['id']), safe='')}/stop")
            except EFluxError as err:
                raise HomeAssistantError("Stopverzoek mislukt of uitkomst onbekend; controleer E-Flux") from err

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
            if self.entry.data.get("pending_since"):
                raise HomeAssistantError("The start outcome is unknown; retry is blocked")
            if self._last_resolution in SUCCESS:
                self.last_start = "E-Flux bevestigt het startverzoek"
                self.async_set_updated_data({**(self.data or {}), "last_start": self.last_start})
                return
            if self._last_resolution in {"NO_RECORD", "ERROR"}:
                # Nothing is running, but neither is a refusal confirmed: no retry.
                raise HomeAssistantError(self.last_start)
            if self._last_resolution not in FAILURE:
                raise HomeAssistantError("The start outcome is unknown; retry is blocked")
            if attempt < 2 and time.monotonic() < deadline:
                await asyncio.sleep(15)
            else:
                break
        raise HomeAssistantError("The charging session did not become active")
