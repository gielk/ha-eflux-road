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
        self.logger = kwargs["logger"]
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
        self.start_status = 500
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
            raise c.EFluxRequestError(self.start_status)
        if path.endswith("/stop"):
            self.stops.append(path)
            # Road answers 500 for a finished transaction, yet closes the request.
            for item in self.history:
                session = item.get("activeSession") or {}
                if path == f"/1/remote-sessions/msp/{item['id']}/stop" and session.get("status") == "COMPLETED":
                    item.update(status="ERROR", stopRequestedAt="2026-11-10T20:25:52Z")
            raise c.EFluxRequestError(500)
        raise AssertionError(path)


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry, self.road = Entry(), Road()
        self.coordinator = c.EFluxCoordinator(types.SimpleNamespace(config_entries=Entries()), self.entry, self.road)

        async def session():
            pass
        self.coordinator.ensure_session = session

        async def no_sleep(_seconds):
            pass
        sleeper = patch.object(c.asyncio, "sleep", no_sleep)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    async def start_with_500(self):
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()

    async def test_completed_records_do_not_count_as_active(self):
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["active"], 0)
        await self.start_with_500()
        self.assertEqual(self.road.starts, 1)

    async def test_unknown_outcome_remains_blocked_within_timeout_even_after_reload(self):
        await self.start_with_500()
        self.entry.data["pending_since"] -= c.PENDING_TIMEOUT - 30
        other = c.EFluxCoordinator(types.SimpleNamespace(config_entries=Entries()), self.entry, self.road)
        other.ensure_session = self.coordinator.ensure_session
        await other._async_update_data()
        with self.assertRaises(c.HomeAssistantError):
            await other.start_once()
        self.assertEqual(self.road.starts, 1)

    async def test_unknown_outcome_without_any_record_expires_after_timeout(self):
        await self.start_with_500()
        self.assertEqual(self.coordinator.last_start, "Uitkomst controleren (HTTP 500)")
        self.entry.data["pending_since"] -= c.PENDING_TIMEOUT + 1
        data = await self.coordinator._async_update_data()
        self.assertNotIn("pending_since", self.entry.data)
        self.assertNotIn("pending_known_ids", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "NO_RECORD")
        self.assertEqual(data["last_start"], "Geen sessie aangemaakt door E-Flux")
        await self.start_with_500()
        self.assertEqual(self.road.starts, 2)

    async def test_expiry_needs_an_empty_account_a_baseline_and_no_new_request(self):
        await self.start_with_500()
        self.entry.data["pending_since"] -= c.PENDING_TIMEOUT + 1
        self.road.active = [{"status": "ACTIVE"}]
        await self.coordinator._async_update_data()
        self.assertIn("pending_since", self.entry.data)
        self.road.active = []
        self.road.history.append({"id": "new", "status": "STARTING", "connectorId": "1",
                                  "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
        await self.coordinator._async_update_data()
        self.assertIn("pending_since", self.entry.data)
        self.road.history.pop()
        baseline = self.entry.data.pop("pending_known_ids")
        await self.coordinator._async_update_data()
        self.assertIn("pending_since", self.entry.data)
        self.entry.data["pending_known_ids"] = baseline
        await self.coordinator._async_update_data()
        self.assertNotIn("pending_since", self.entry.data)

    async def test_refusal_without_a_new_request_unblocks_at_once(self):
        self.road.start_status = 412
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_until_confirmed()
        self.assertEqual(self.road.starts, 1)
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "REJECTED")
        self.assertEqual(self.coordinator.last_start, "Geweigerd door E-Flux (HTTP 412)")
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertEqual(self.road.starts, 2)

    async def test_refusal_with_a_new_request_keeps_the_outcome_open(self):
        self.road.start_status = 412
        original = self.road.request

        async def request(method, path, **kwargs):
            if path == "/1/remote-sessions/msp":
                self.road.history.append({"id": "new", "status": "STARTING", "connectorId": "1",
                                          "evseId": "station"})
            return await original(method, path, **kwargs)
        self.road.request = request
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator.last_start, "Uitkomst controleren (HTTP 412)")

    async def test_refused_authentication_on_the_start_does_not_block(self):
        original = self.road.request

        async def request(method, path, **kwargs):
            if path == "/1/remote-sessions/msp":
                self.road.starts += 1
                raise c.EFluxAuthError("E-Flux authentication failed")
            return await original(method, path, **kwargs)
        self.road.request = request
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_until_confirmed()
        self.assertEqual(self.road.starts, 1)
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator.last_start, "E-Flux authentication failed")

    async def test_new_request_in_error_without_session_unblocks_but_is_not_retried(self):
        original = self.road.request

        async def request(method, path, **kwargs):
            if path == "/1/remote-sessions/msp":
                self.road.history.append({"id": f"new{self.road.starts}", "status": "ERROR",
                                          "connectorId": "1", "evseId": "station",
                                          "createdAt": datetime.now(timezone.utc).isoformat()})
            return await original(method, path, **kwargs)
        self.road.request = request

        async def refresh():
            self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.async_request_refresh = refresh
        with self.assertRaises(c.HomeAssistantError) as raised:
            await self.coordinator.start_until_confirmed()
        self.assertEqual(self.road.starts, 1)
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "ERROR")
        self.assertEqual(str(raised.exception), "Start niet gelukt volgens E-Flux (ERROR)")

    async def test_new_request_in_error_with_live_session_counts_as_started(self):
        await self.start_with_500()
        self.road.history.append({"id": "new", "status": "ERROR", "connectorId": "1", "evseId": "station",
                                  "createdAt": datetime.now(timezone.utc).isoformat(),
                                  "activeSessionId": "live", "activeSession": {"id": "live", "status": "ACTIVE"}})
        data = await self.coordinator._async_update_data()
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator._last_resolution, "ACTIVE")
        self.assertEqual(data["last_start"], "E-Flux bevestigt het startverzoek")

    async def test_failed_precheck_is_not_reported_as_a_sent_request(self):
        async def request(method, path, **kwargs):
            raise c.EFluxRequestError(503)
        self.road.request = request
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertNotIn("pending_since", self.entry.data)
        self.assertEqual(self.coordinator.last_start, "E-Flux controle mislukt (HTTP 503)")

    async def test_outcome_text_follows_the_request_without_a_waiting_caller(self):
        for outcome, text in (("CANCELLED", "Start niet gelukt volgens E-Flux (CANCELLED)"),
                              ("ACTIVE", "E-Flux bevestigt het startverzoek")):
            self.entry.data = {key: value for key, value in self.entry.data.items()
                               if not key.startswith("pending")}
            self.road.history = [{"id": "old", "status": "COMPLETED"}]
            await self.start_with_500()
            self.road.history.append({"id": "new", "status": outcome, "connectorId": "1",
                                      "evseId": "station", "createdAt": datetime.now(timezone.utc).isoformat()})
            data = await self.coordinator._async_update_data()
            self.assertEqual(data["last_start"], text)

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
                       [{"status": "ERROR"}], [{"status": "COMPLETED"}] * 50):
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

    async def test_completed_linked_session_resolves_stale_remote_active(self):
        self.road.history[0] = {
            "id": "remote-old", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
            "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
        }
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["active"], 0)
        self.assertEqual(data["remote_connector_raw"], "ACTIVE")
        self.assertEqual(data["remote_connector"], "COMPLETED")
        self.assertIsNotNone(datetime.fromisoformat(data["last_successful_update"]).tzinfo)
        self.assertIsNone(data["session_energy"])
        await self.start_with_500()
        self.assertEqual(self.road.starts, 1)
        # Road refuses a start while this request is ACTIVE, so it is closed first.
        self.assertEqual(self.road.stops, ["/1/remote-sessions/msp/remote-old/stop"])
        self.assertEqual(self.road.history[0]["status"], "ERROR")
        self.assertEqual(self.entry.data["pending_known_ids"], ["remote-old"])
        # A 500 still has an unknown outcome: a second request must not be sent.
        await self.start_with_500()
        self.assertEqual(self.road.starts, 1)
        self.assertEqual(len(self.road.stops), 1)

    async def test_closed_request_is_finished_only_without_a_live_session(self):
        self.road.history[0] = {
            "id": "remote-old", "status": "ERROR", "connectorId": "1", "evseId": "station",
            "createdAt": "2026-11-10T15:46:34Z", "stopRequestedAt": "2026-11-10T20:25:52Z",
            "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
        }
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["remote_connector_raw"], "ERROR")
        self.assertEqual(data["remote_connector"], "COMPLETED")
        await self.start_with_500()
        self.assertEqual(self.road.starts, 1)
        self.assertFalse(self.road.stops)
        self.entry.data = {key: value for key, value in self.entry.data.items()
                           if not key.startswith("pending")}
        self.road.history[0]["activeSession"]["status"] = "ACTIVE"
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertEqual(self.road.starts, 1)

    async def test_stale_request_that_stays_active_is_not_stopped_twice(self):
        self.road.history[0] = {
            "id": "remote-old", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
            "stopRequestedAt": "2026-11-10T20:25:52Z",
            "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
        }
        await self.start_with_500()
        self.assertFalse(self.road.stops)
        self.assertEqual(self.road.starts, 1)

    async def test_diagnostics_ignore_other_connectors_and_preserve_uncertainty(self):
        self.road.history = [
            {"id": "a", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
             "createdAt": "2026-01-01T00:00:00Z", "activeSessionId": "one",
             "activeSession": {"id": "one", "status": "COMPLETED"}},
            {"id": "b", "status": "ACTIVE", "connectorId": "2", "evseId": "station",
             "createdAt": "2026-01-02T00:00:00Z"},
        ]
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["remote_connector"], "COMPLETED")
        self.assertEqual(data["remote_connector_raw"], "ACTIVE")
        self.road.active = [{"status": "ACTIVE"}]
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["remote_connector"], "ACTIVE")

    async def test_stale_active_is_unblocked_only_with_matching_completed_session(self):
        for change in (
            {"activeSessionId": None},
            {"activeSession": {"id": "different", "status": "COMPLETED"}},
            {"activeSession": {"id": "finished", "status": "ACTIVE"}},
            {"activeSession": {"id": "finished"}},
            {"evseId": "another-station"},
            {"status": "STARTING"},
        ):
            self.road.history[0] = {
                "id": "remote-old", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
                "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
                **change,
            }
            with self.assertRaises(c.HomeAssistantError, msg=repr(change)):
                await self.coordinator.start_once()
            self.assertEqual(self.road.starts, 0)

    async def test_completed_embedded_session_does_not_override_live_account_session(self):
        self.road.history[0] = {
            "id": "remote-old", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
            "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
        }
        self.road.active = [{"status": "ACTIVE"}]
        with self.assertRaises(c.HomeAssistantError):
            await self.coordinator.start_once()
        self.assertEqual(self.road.starts, 0)

    async def test_pending_stop_resolves_only_when_linked_session_is_finished(self):
        self.entry.data["pending_stop"] = "remote-old"
        self.road.history[0] = {
            "id": "remote-old", "status": "ACTIVE", "connectorId": "1", "evseId": "station",
            "activeSessionId": "finished", "activeSession": {"id": "finished", "status": "COMPLETED"},
        }
        self.road.active = [{"status": "ACTIVE"}]
        await self.coordinator._async_update_data()
        self.assertIn("pending_stop", self.entry.data)
        self.road.active = []
        self.road.history[0]["activeSession"]["id"] = "different"
        await self.coordinator._async_update_data()
        self.assertIn("pending_stop", self.entry.data)
        self.road.history[0]["activeSession"]["id"] = "finished"
        await self.coordinator._async_update_data()
        self.assertNotIn("pending_stop", self.entry.data)

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
