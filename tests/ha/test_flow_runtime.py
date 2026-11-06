"""Test real Home Assistant flow machinery with synthetic Road responses."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eflux_road.const import DOMAIN


@pytest.fixture
def client():
    with patch("custom_components.eflux_road.config_flow.EFluxClient") as cls:
        instance = cls.return_value
        instance.token = "synthetic"
        instance.validate = AsyncMock(return_value={"email": "example@example.invalid"})

        async def request(method, path, **kwargs):
            if path == "/1/cards/mine":
                return [{"status": "ACTIVE", "tokenId": "t", "token": {"id": "t", "visualNumber": "TEST1234"}}]
            if path == "/1/evse-controllers/mine":
                return [{"locationId": "location"}]
            if path == "/1/map/locations":
                return [{"id": "location", "name": "Example", "evses": [{"uid": "uid", "evse_id": "evse",
                         "connectors": [{"id": "1"}]}]}]
            raise AssertionError(path)

        instance.request = AsyncMock(side_effect=request)
        yield instance


async def test_setup_selects_discovered_card_and_station(hass, client):
    with patch("custom_components.eflux_road.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == "menu"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "session"})
        assert result["step_id"] == "session"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"session_token": "synthetic"})
        assert result["step_id"] == "resources"
        result = await hass.config_entries.flow.async_configure(result["flow_id"],
                                                                  {"card_identifier": "TEST1234", "station": "0"})
        assert result["type"] == "create_entry"
        assert result["data"]["evse_id"] == "evse"
        assert result["data"]["card_identifier"] == "TEST1234"


async def test_options_keep_existing_entry_and_select_new_resources(hass, client):
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": "synthetic", "evse_id": "old",
        "evse_uid": "old-uid", "location_id": "old-location", "connector_id": "1", "card_identifier": "old-card"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"],
                                                                {"card_identifier": "TEST1234", "station": "0"})
    assert result["type"] == "create_entry"
    assert entry.options["evse_id"] == "evse"
    assert entry.data["session_token"] == "synthetic"


async def test_options_are_blocked_while_a_start_is_unresolved(hass, client):
    client.request.side_effect = Exception("unused")
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": "synthetic", "evse_id": "old",
        "evse_uid": "old-uid", "location_id": "old-location", "connector_id": "1",
        "card_identifier": "old-card", "pending_since": 123})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "abort"
    assert result["reason"] == "start_pending"


async def test_entry_loads_entities_and_data_updates_do_not_reload(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": "synthetic", "evse_id": "evse",
        "evse_uid": "uid", "location_id": "location", "connector_id": "1", "card_identifier": "TEST1234"})
    entry.add_to_hass(hass)

    async def request(method, path, **kwargs):
        if path == "/1/cards/mine":
            return [{"status": "ACTIVE", "tokenId": "t", "token": {"id": "t", "visualNumber": "TEST1234"}}]
        if path == "/1/active-sessions/mine":
            return []
        if path == "/1/remote-sessions/msp/mine":
            return []
        if path == "/1/map/locations":
            return [{"id": "location", "name": "Example", "evses": [{"uid": "uid", "evse_id": "evse",
                "status": "AVAILABLE", "maxPower": 11, "connectors": [{"id": "1"}]}]}]
        raise AssertionError(path)

    with (patch("custom_components.eflux_road.EFluxClient") as cls,
          patch("custom_components.eflux_road.coordinator.EFluxCoordinator.ensure_session", new_callable=AsyncMock)):
        cls.return_value.request = AsyncMock(side_effect=request)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        states = {state.entity_id: state for state in hass.states.async_all()}
        assert any(s.entity_id.startswith("binary_sensor.") and s.state == "on" for s in states.values())
        assert any(s.entity_id.startswith("sensor.") and s.state == "11" for s in states.values())
        assert len([key for key in states if key.startswith("button.")]) == 3
        coordinator = hass.data[DOMAIN][entry.entry_id]
        hass.config_entries.async_update_entry(entry, data={**entry.data, "pending_since": 1})
        await hass.async_block_till_done()
        assert hass.data[DOMAIN][entry.entry_id] is coordinator
