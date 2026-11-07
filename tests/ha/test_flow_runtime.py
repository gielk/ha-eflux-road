"""Test real Home Assistant flow machinery with synthetic Road responses."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eflux_road.api import EFluxError, EFluxMfaRequired, EFluxRequestError
from custom_components.eflux_road.const import DOMAIN


@pytest.fixture
def client():
    with patch("custom_components.eflux_road.config_flow.EFluxClient") as cls:
        instance = cls.return_value
        instance.token = "synthetic"
        instance.login = AsyncMock(return_value="synthetic")
        instance.validate = AsyncMock(return_value={"email": "example@example.invalid"})
        instance.send_sms_code = AsyncMock()
        instance.verify_mfa = AsyncMock(return_value="new-session")

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


async def begin_mfa(hass, client, source="user", entry=None):
    client.login.side_effect = EFluxMfaRequired("pending-private", ("totp", "sms"))
    context = {"source": source}
    data = None
    if entry is not None:
        context["entry_id"] = entry.entry_id
        data = entry.data
    result = await hass.config_entries.flow.async_init(DOMAIN, context=context, data=data)
    login_step = "reauth_login" if entry is not None else "login"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": login_step})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {
        "username": "example@example.invalid", "password": "synthetic-password"})


async def test_totp_setup_accepts_a_one_time_code_without_saving_it(hass, client):
    with patch("custom_components.eflux_road.async_setup_entry", return_value=True):
        result = await begin_mfa(hass, client)
        assert result["step_id"] == "mfa_method"
        assert set(result["menu_options"]) == {"mfa_totp", "mfa_sms"}
        flow_id = result["flow_id"]
        result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "mfa_totp"})
        assert result["step_id"] == "mfa_totp"
        result = await hass.config_entries.flow.async_configure(flow_id, {"code": "123"})
        assert result["errors"]["base"] == "mfa_code_format"
        client.verify_mfa.assert_not_awaited()
        client.verify_mfa.side_effect = [EFluxRequestError(400), "new-session"]
        result = await hass.config_entries.flow.async_configure(flow_id, {"code": "111111"})
        assert result["errors"]["base"] == "mfa_invalid_or_expired"
        result = await hass.config_entries.flow.async_configure(flow_id, {"code": "012345"})
        assert result["step_id"] == "resources"
        client.verify_mfa.assert_awaited_with("pending-private", "totp", "012345")
        result = await hass.config_entries.flow.async_configure(flow_id,
                    {"card_identifier": "TEST1234", "station": "0"})
        assert result["type"] == "create_entry"
        assert result["data"]["session_token"] == "new-session"
        assert all(secret not in str(result["data"]) for secret in (
            "012345", "pending-private", "synthetic-password"))


async def test_sms_is_sent_only_when_selected_and_not_resent_for_a_bad_code(hass, client):
    result = await begin_mfa(hass, client)
    flow_id = result["flow_id"]
    client.send_sms_code.assert_not_awaited()
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "mfa_sms"})
    assert result["step_id"] == "mfa_sms"
    client.send_sms_code.assert_awaited_once_with("pending-private")
    client.verify_mfa.side_effect = EFluxRequestError(429)
    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "123456"})
    assert result["errors"]["base"] == "mfa_rate_limited"
    client.send_sms_code.assert_awaited_once()


async def test_mfa_validation_retry_does_not_reuse_the_one_time_code(hass, client):
    client.validate.side_effect = [EFluxError("offline"), {"email": "example@example.invalid"}]
    result = await begin_mfa(hass, client)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "mfa_totp"})
    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "012345"})
    assert result["step_id"] == "mfa_validate"
    assert result["errors"]["base"] == "cannot_connect"
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "resources"
    client.verify_mfa.assert_awaited_once_with("pending-private", "totp", "012345")
    assert client.validate.await_count == 2


async def test_reauthentication_with_totp_updates_only_the_session_and_username(hass, client):
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": "old-session", "evse_id": "evse"})
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await begin_mfa(hass, client, source="reauth", entry=entry)
        flow_id = result["flow_id"]
        await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "mfa_totp"})
        result = await hass.config_entries.flow.async_configure(flow_id, {"code": "012345"})
        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert entry.data["session_token"] == "new-session"
        assert entry.data["evse_id"] == "evse"
        assert all(secret not in str(entry.data) for secret in (
            "012345", "pending-private", "synthetic-password"))


async def test_reauthentication_rejects_a_different_account(hass, client):
    entry = MockConfigEntry(domain=DOMAIN, data={"session_token": "old-session", "evse_id": "evse",
                                                "username": "original@example.invalid"})
    entry.add_to_hass(hass)
    result = await begin_mfa(hass, client, source="reauth", entry=entry)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "mfa_totp"})
    result = await hass.config_entries.flow.async_configure(flow_id, {"code": "012345"})
    assert result["type"] == "abort"
    assert result["reason"] == "wrong_account"
    assert entry.data["session_token"] == "old-session"


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
