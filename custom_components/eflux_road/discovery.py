"""Parse account resources without guessing IDs or exposing card numbers in labels."""

from __future__ import annotations

from typing import Any


def walk(value: Any, depth: int = 0):
    """Yield response dictionaries, bounded for malformed or unexpected input."""
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, depth + 1)


def cards(data: Any) -> dict[str, str]:
    """Return unambiguous selectable active cards, keyed by their visual identifier."""
    found: dict[str, list[str]] = {}
    for card in walk(data):
        token = card.get("token")
        if not (str(card.get("status", "")).upper() == "ACTIVE"
                and isinstance(token, dict) and isinstance(card.get("tokenId"), str)
                and card["tokenId"] == token.get("id")):
            continue
        identifier = next((token.get(key) for key in ("visualNumber", "customId", "cardNumber")
                           if isinstance(token.get(key), str) and token[key]), None)
        if not identifier:
            continue
        masked = "••••" + identifier[-4:]
        found.setdefault(identifier, []).append(f"{masked} (ACTIVE)")
    return {identifier: labels[0] for identifier, labels in found.items() if len(labels) == 1}


def location_ids(data: Any) -> list[str]:
    """Find explicit location IDs; controller IDs are not location IDs."""
    ids = set()
    for item in walk(data):
        for key in ("locationId", "location_id"):
            if isinstance(item.get(key), str) and item[key]:
                ids.add(item[key])
        location = item.get("location")
        if isinstance(location, dict) and isinstance(location.get("id"), str):
            ids.add(location["id"])
    return sorted(ids)


def stations(locations: Any) -> list[dict[str, str]]:
    """Accept only map EVSEs with all IDs required for a remote start."""
    result = []
    if not isinstance(locations, list):
        return result
    for location in locations:
        if not isinstance(location, dict) or not isinstance(location.get("id"), str):
            continue
        for evse in location.get("evses", []):
            if not isinstance(evse, dict) or not all(isinstance(evse.get(k), str) and evse[k]
                                                      for k in ("uid", "evse_id")):
                continue
            for connector in evse.get("connectors", []):
                if not isinstance(connector, dict) or connector.get("id") is None:
                    continue
                result.append({"location_id": location["id"], "evse_uid": evse["uid"],
                               "evse_id": evse["evse_id"], "connector_id": str(connector["id"]),
                               "label": f"{location.get('name') or location.get('address') or 'Laadpunt'} — {evse['evse_id']} / {connector['id']}"})
    return result
