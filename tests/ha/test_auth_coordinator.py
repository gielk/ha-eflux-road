"""Test that server-directed renewal is serialized and preserves entry data."""

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


async def test_refresh_hint_is_handled_once_with_concurrent_requests(hass):
    old_token, new_token = token(7200), token(14400)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": old_token, "pending_since": 123})
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=old_token, refresh_requested=True)

    async def refresh():
        await asyncio.sleep(0)
        client.token = new_token
        client.refresh_requested = False
        return new_token

    client.refresh = AsyncMock(side_effect=refresh)
    coordinator = EFluxCoordinator(hass, entry, client)
    await asyncio.gather(*(coordinator.ensure_session() for _ in range(4)))
    client.refresh.assert_awaited_once()
    assert entry.data["session_token"] == new_token
    assert entry.data["pending_since"] == 123


async def test_expiring_token_refreshes_and_failure_preserves_old_session(hass):
    old_token = token(300)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": old_token})
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=old_token, refresh_requested=False,
                             refresh=AsyncMock(side_effect=EFluxAuthError("blocked")))
    coordinator = EFluxCoordinator(hass, entry, client)
    with pytest.raises(EFluxAuthError):
        await coordinator.ensure_session()
    assert entry.data["session_token"] == old_token
    client.refresh.assert_awaited_once()


async def test_fresh_token_without_server_hint_is_not_refreshed(hass):
    fresh = token(7200)
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": fresh})
    entry.add_to_hass(hass)
    client = SimpleNamespace(token=fresh, refresh_requested=False, refresh=AsyncMock())
    await EFluxCoordinator(hass, entry, client).ensure_session()
    client.refresh.assert_not_awaited()
