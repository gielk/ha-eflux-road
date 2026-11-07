"""Verify the remote session reason has a stable optional attribute."""
from types import SimpleNamespace

import pytest

from custom_components.eflux_road.const import CONF_EVSE_ID
from custom_components.eflux_road.sensor import EFluxSensor, SENSORS


@pytest.mark.parametrize("reason", [None, "REJECTED_BY_CPO"])
async def test_cancelled_reason_is_present_when_unknown_and_when_reported(hass, reason):
    coordinator = SimpleNamespace(data={"remote_reason": reason})
    entry = SimpleNamespace(entry_id="test", options={}, data={CONF_EVSE_ID: "test-evse"})
    description = next(item for item in SENSORS if item.key == "remote")
    sensor = EFluxSensor(coordinator, entry, description)
    assert sensor.extra_state_attributes == {"cancelled_reason": reason}
