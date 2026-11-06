"""Exercise the start decision against fake Home Assistant and Road responses."""

import asyncio
import importlib
import sys
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


def module(name):
    value = types.ModuleType(name)
    sys.modules[name] = value
    return value


for name in ("homeassistant", "homeassistant.config_entries", "homeassistant.core",
             "homeassistant.exceptions", "homeassistant.helpers",
             "homeassistant.helpers.update_coordinator"):
    module(name)
sys.modules["homeassistant.config_entries"].ConfigEntry = object
sys.modules["homeassistant.core"].HomeAssistant = object
for name in ("ConfigEntryAuthFailed", "HomeAssistantError"):
    setattr(sys.modules["homeassistant.exceptions"], name, type(name, (Exception,), {}))


class Coordinator:
    def __init__(self, *args, **kwargs):
        self.hass = args[0]
        self.data = {}
        self.last_update_success = True

    def async_set_updated_data(self, data):
        self.data = data


update = sys.modules["homeassistant.helpers.update_coordinator"]
update.DataUpdateCoordinator = Coordinator
update.UpdateFailed = type("UpdateFailed", (Exception,), {})
package = module("eflux_road")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "eflux_road")]
c = importlib.import_module("eflux_road.coordinator")


class Entry:
    def __init__(self):
        self.data = {"evse_id": "station", "evse_uid": "uid", "connector_id": "1",
                     "location_id": "location", "card_identifier": "card"}
        self.options = {}


class Entries:
    def async_update_entry(self, entry, data):
        entry.data = data


class Road:
    def __init__(self):
        self.active = [{"status": "COMPLETED"} for _ in range(9)]
        self.history = [{"id": "old", "status": "COMPLETED"}]
        self.starts = 0
        self.stops = []

    async def request(self, method, path, **kwargs):
        if path == "/1/cards/mine":
            return [{"status": "ACTIVE", "tokenId": "token", "token": {"id": "token", "visualNumber": "card"}}]
        if path == "/1/active-sessions/mine":
            return self.active
        if path == "/1/remote-sessions/msp/mine":
            return self.history
        if path == "/1/map/locations":
            return [{"id": "location", "evses": [{"uid": "uid", "evse_id": "station",
                "status": "CHARGING", "capabilities": ["REMOTE_START_STOP_CAPABLE"],
                "connectors": [{"id": "1"}]}]}]
        if path == "/1/remote-sessions/msp":
            self.starts += 1
            raise c.EFluxRequestError(500)
        if path.endswith("/stop"):
            self.stops.append(path)
            raise c.EFluxRequestError(500)
        raise AssertionError(path)


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry, self.road = Entry(), Road()
        self.coordinator = c.EFluxCoordinator(types.SimpleNamespace(config_entries=Entries()), self.entry, self.road)

        async def session():
            pass
        self.coordinator.ensure_session = session

    async def start_with_500(self):
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()

    async def test_completed_records_do_not_count_as_active(self):
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["active"], 0)
        await self.start_with_500()
        self.assertEqual(self.road.starts, 1)

    async def test_unknown_outcome_remains_blocked_even_after_timeout_and_reload(self):
        await self.start_with_500()
        self.entry.data["pending_since"] -= 1000
        other = c.EFluxCoordinator(types.SimpleNamespace(config_entries=Entries()), self.entry, self.road)
        other.ensure_session = self.coordinator.ensure_session
        await other._async_update_data()
        with self.assertRaises(c.HomeAssistantError):
            await other.start_once()
        self.assertEqual(self.road.starts, 1)

    async def test_old_or_unrelated_success_never_clears_pending(self):
        await self.start_with_500()
        self.road.active = [{"status": "ACTIVE"}]
        self.road.history[0].update(status="COMPLETED", connectorId="1", evseId="station",
                                    createdAt=datetime.now(timezone.utc).isoformat())
        await self.coordinator._async_update_data()
        self.assertIn("pending_since", self.entry.data)

    async def test_new_matching_rejection_clears_pending(self):
        await self.start_with_500()
        self.road.history.append({"id": "new", "status": "CANCELLED", "connectorId": "1",
                                  "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
        await self.coordinator._async_update_data()
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "CANCELLED")

    async def test_new_matching_success_clears_pending(self):
        await self.start_with_500()
        self.road.history.append({"id": "new", "status": "ACTIVE", "connectorId": "1",
                                  "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
        await self.coordinator._async_update_data()
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "ACTIVE")

    async def test_multiple_new_records_do_not_clear_pending(self):
        await self.start_with_500()
        for identity in ("new1", "new2"):
            self.road.history.append({"id": identity, "status": "CANCELLED", "connectorId": "1",
                                      "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
        await self.coordinator._async_update_data()
        self.assertIn("pending_since", self.entry.data)

    async def test_retry_deadline_keeps_uncertain_start_blocked(self):
        clock = types.SimpleNamespace(time=time.time, monotonic=iter([0, 151]).__next__)
        with patch.object(c, "time", clock):
            with self.assertRaises(c.HomeAssistantError):
                await self.coordinator.start_until_confirmed()
        self.assertIn("pending_since", self.entry.data)
        self.assertEqual(self.road.starts, 1)

    async def test_active_unknown_or_incomplete_session_list_blocks(self):
        for active in ([{"status": "ACTIVE"}], [{"status": "FUTURE_STATUS"}],
                       [{"status": "COMPLETED"}] * 50):
            self.road.active = active
            with self.assertRaises(c.HomeAssistantError):
                await self.coordinator.start_once()
        self.assertEqual(self.road.starts, 0)

    async def test_old_unresolved_request_still_blocks(self):
        self.road.history[0]["status"] = "STARTING"
        self.road.history[0]["createdAt"] = "2020-01-01T00:00:00Z"
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertEqual(self.road.starts, 0)

    async def test_stop_is_sent_only_once_for_the_selected_remote_session(self):
        self.road.history.append({"id": "remote", "status": "ACTIVE", "connectorId": "1",
                                  "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
        for _ in range(2):
            with self.assertRaises(c.HomeAssistantError):
                await self.coordinator.stop_once()
        self.assertEqual(self.road.stops, ["/1/remote-sessions/msp/remote/stop"])
        self.assertEqual(self.entry.data["pending_stop"], "remote")
        self.road.history[-1]["status"] = "COMPLETED"
        await self.coordinator._async_update_data()
        self.assertNotIn("pending_stop", self.entry.data)

    async def test_stop_refuses_unmatched_or_ambiguous_sessions(self):
        for evse in ("other", "station", "station"):
            self.road.history.append({"id": str(len(self.road.history)), "status": "ACTIVE",
                                      "connectorId": "1", "evseId": evse})
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.stop_once()
        self.assertFalse(self.road.stops)

    async def test_session_energy_belongs_to_matching_remote_session(self):
        self.road.history.append({"id": "remote", "status": "ACTIVE", "connectorId": "1",
                                  "evseId": "station", "activeSession": {"kwh": 12.3,
                                  "startedAt": "2026-11-06T12:00:00Z", "chargingState": "CHARGING"}})
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["session_energy"], 12.3)
        self.road.history[-1]["evseId"] = "other"
        data = await self.coordinator._async_update_data()
        self.assertIsNone(data["session_energy"])


if __name__ == "__main__":
    unittest.main()
