"""Test safe, paired E-Flux session renewal."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eflux_road.api import EFluxAuthError
from custom_components.eflux_road.const import DOMAIN
from custom_components.eflux_road.coordinator import EFluxCoordinator


def token(expires_in):
    payload = json.dumps({"exp": time.time() + expires_in}).encode()
    return "header." + base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".signature"


async def test_refresh_hint_rotates_and_persists_the_complete_pair_once(hass):
    old_token, new_token = token(7200), token(14400)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": old_token, "pending_since": 123})
    entry.add_to_hass(hass)
    client = SimpleNamespace(
        token=old_token,
        device_token="old-device",
        refresh_requested=True,
    )

    async def refresh():
        await asyncio.sleep(0)
        client.token = new_token
        client.device_token = "new-device"
        client.refresh_requested = False
        return new_token, "new-device"

    client.refresh = AsyncMock(side_effect=refresh)
    coordinator = EFluxCoordinator(hass, entry, client)
    await asyncio.gather(*(coordinator.ensure_session() for _ in range(4)))
    client.refresh.assert_awaited_once()
    assert entry.data["session_token"] == new_token
    assert entry.data["device_token"] == "new-device"
    assert entry.data["pending_since"] == 123


async def test_expiring_token_without_device_credential_requires_reauthentication(hass):
    old_token = token(300)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": old_token})
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=old_token, device_token=None, refresh_requested=False)
    coordinator = EFluxCoordinator(hass, entry, client)
    with pytest.raises(EFluxAuthError):
        await coordinator.ensure_session()
    assert entry.data["session_token"] == old_token


async def test_fresh_token_without_server_hint_stays_usable(hass):
    fresh = token(7200)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": fresh})
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=fresh, device_token=None, refresh_requested=False)
    await EFluxCoordinator(hass, entry, client).ensure_session()


async def test_server_renewal_hint_refreshes_before_a_remote_action(hass):
    current = token(7200)
    renewed = token(14400)
    entry = MockConfigEntry(domain=DOMAIN, data={
        "session_token": current,
        "location_id": "location",
        "evse_id": "evse",
        "evse_uid": "uid",
        "connector_id": "1",
        "card_identifier": "TEST1234",
    })
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=current, device_token="device", refresh_requested=True)

    async def refresh():
        client.token = renewed
        client.device_token = "rotated-device"
        client.refresh_requested = False
        return renewed, "rotated-device"

    client.refresh = AsyncMock(side_effect=refresh)
    await EFluxCoordinator(hass, entry, client).ensure_session()
    client.refresh.assert_awaited_once()
    assert entry.data["session_token"] == renewed
    assert entry.data["device_token"] == "rotated-device"
