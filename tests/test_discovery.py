"""Test conservative resource discovery without real accounts or identifiers."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("discovery", Path(__file__).resolve().parents[1] /
                                          "custom_components/eflux_road/discovery.py")
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class DiscoveryTests(unittest.TestCase):
    def test_cards_require_active_consistent_unique_token(self):
        card = {"status": "ACTIVE", "tokenId": "t", "token": {"id": "t", "visualNumber": "TEST1234"}}
        self.assertEqual(discovery.cards([card]), {"TEST1234": "••••1234 (ACTIVE)"})
        self.assertEqual(discovery.cards([card, card]), {})
        self.assertEqual(discovery.cards([{**card, "status": "DISABLED"}]), {})
        self.assertEqual(discovery.cards([{**card, "tokenId": "wrong"}]), {})

    def test_stations_require_all_explicit_map_identifiers(self):
        self.assertEqual(discovery.location_ids([{"id": "controller"}, {"locationId": "location"}]), ["location"])
        point = {"id": "location", "name": "Example", "evses": [{"uid": "uid", "evse_id": "evse",
                  "connectors": [{"id": "1"}]}]}
        result = discovery.stations([point])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["connector_id"], "1")
        del point["evses"][0]["uid"]
        self.assertEqual(discovery.stations([point]), [])


if __name__ == "__main__":
    unittest.main()
