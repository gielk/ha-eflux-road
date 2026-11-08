"""Configuration flow for E-Flux by Road."""

from __future__ import annotations

import json

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import EFluxAuthError, EFluxClient, EFluxError, EFluxVerificationRequired
from .const import (
    CONF_CARD_ID, CONF_CONNECTOR_ID, CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID,
    CONF_DEVICE_TOKEN, CONF_SESSION_TOKEN, DOMAIN,
)
from .discovery import cards, location_ids, stations

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
MANUAL = "__manual__"


def session_export(value: str) -> tuple[str, str | None]:
    """Accept either a JWT or the masked config-flow browser export.

    The export has only the two values the dashboard itself uses to renew a
    session. It is never logged and the selector masks it in Home Assistant.
    """
    raw = value.strip()
    try:
        exported = json.loads(raw)
    except (TypeError, ValueError):
        exported = None
    if isinstance(exported, dict):
        token = exported.get("token")
        device_token = exported.get("device", exported.get("deviceToken"))
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Missing session token")
        if device_token is not None and (not isinstance(device_token, str) or not device_token.strip()):
            raise ValueError("Invalid device token")
        return token.strip(), device_token.strip() if isinstance(device_token, str) else None
    # Config-flow translations use braces for placeholders. The on-screen
    # instructions therefore copy the two values as `jwt|device` instead of
    # a JSON object. A JWT cannot contain a pipe, making this unambiguous.
    if "|" in raw:
        token, device_token = raw.split("|", 1)
        if not token.strip() or not device_token.strip():
            raise ValueError("Invalid session export")
        return token.strip(), device_token.strip()
    raw = raw.strip("\"'")
    if raw.startswith("Bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise ValueError("Missing session token")
    return raw, None


async def discover(client):
    """Discover resources where the account exposes them; allow manual fallback."""
    try:
        found_cards = cards(await client.request("GET", "/1/cards/mine"))
    except EFluxError:
        found_cards = {}
    found_stations = []
    try:
        controllers = await client.request("POST", "/1/evse-controllers/mine", payload={"limit": 100})
        ids = location_ids(controllers)
        if ids:
            locations = await client.request("POST", "/1/map/locations", payload={"ids": ids}, auth=False)
            found_stations = stations(locations)
    except EFluxError:
        pass
    return found_cards, found_stations


class EFluxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up an E-Flux account."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._device_token: str | None = None
        self._username: str = ""
        self._cards = None
        self._stations = []
        self._card_id = None

    async def async_step_user(self, user_input=None):
        """Choose how to authenticate."""
        return self.async_show_menu(
            step_id="user", menu_options=["session", "login"],
        )

    async def async_step_login(self, user_input=None):
        """Sign in using an E-Flux account."""
        errors = {}
        if user_input is not None:
            client = EFluxClient(async_get_clientsession(self.hass))
            try:
                self._token = await client.login(
                    user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD]
                )
                await client.validate()
            except EFluxVerificationRequired:
                errors["base"] = "verification_required"
            except EFluxAuthError:
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                self._device_token = client.device_token
                self._username = user_input[CONF_USERNAME].strip()
                return await self.async_step_resources()
        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): PASSWORD,
            }),
            errors=errors,
        )

    async def async_step_session(self, user_input=None):
        """Use a session obtained by signing into E-Flux in the browser."""
        errors = {}
        if user_input is not None:
            try:
                raw_token, device_token = session_export(user_input[CONF_SESSION_TOKEN])
                client = EFluxClient(
                    async_get_clientsession(self.hass), raw_token, device_token,
                )
                user_info = await client.validate()
            except (EFluxAuthError, ValueError):
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                self._token = client.token
                self._device_token = client.device_token
                if not self._username and isinstance(user_info, dict):
                    self._username = user_info.get("email", "")
                return await self.async_step_resources()
        return self.async_show_form(
            step_id="session",
            data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): PASSWORD}),
            errors=errors,
        )

    async def async_step_resources(self, user_input=None):
        """Offer discovered cards and stations without putting IDs in labels."""
        if self._token is None:
            return self.async_abort(reason="authentication_expired")
        if self._cards is None:
            self._cards, self._stations = await discover(EFluxClient(
                async_get_clientsession(self.hass), self._token, self._device_token,
            ))
        manual = "Handmatig invoeren" if self.hass.config.language == "nl" else "Manual entry"
        card_choices = {**self._cards, MANUAL: manual}
        station_choices = {str(i): item["label"] for i, item in enumerate(self._stations)}
        station_choices[MANUAL] = manual
        if user_input is not None:
            self._card_id = user_input[CONF_CARD_ID]
            if user_input["station"] != MANUAL:
                selected = self._stations[int(user_input["station"])]
                self._station = {key: selected[key] for key in (CONF_EVSE_ID, CONF_EVSE_UID,
                                                                 CONF_LOCATION_ID, CONF_CONNECTOR_ID)}
            else:
                self._station = None
            if self._card_id != MANUAL and self._station is not None:
                return await self._create_charger({**self._station, CONF_CARD_ID: self._card_id})
            return await self.async_step_charger()
        return self.async_show_form(step_id="resources", data_schema=vol.Schema({
            vol.Required(CONF_CARD_ID, default=next(iter(self._cards), MANUAL)): vol.In(card_choices),
            vol.Required("station", default=next(iter(station_choices))): vol.In(station_choices),
        }))

    async def _create_charger(self, values):
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            configured = {**entry.data, **entry.options}
            if configured.get(CONF_EVSE_ID) == values[CONF_EVSE_ID].strip():
                return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(values[CONF_EVSE_ID].strip())
        self._abort_if_unique_id_configured()
        data = {
            **{key: value.strip() for key, value in values.items()},
            CONF_SESSION_TOKEN: self._token, CONF_USERNAME: self._username,
        }
        if self._device_token:
            data[CONF_DEVICE_TOKEN] = self._device_token
        return self.async_create_entry(title="E-Flux laadpaal", data=data)

    async def async_step_charger(self, user_input=None):
        """Choose the charging station and the card."""
        if self._token is None:
            return self.async_abort(reason="authentication_expired")
        if user_input is not None:
            values = {**(self._station or {}), **user_input}
            if self._card_id != MANUAL:
                values[CONF_CARD_ID] = self._card_id
            return await self._create_charger(values)
        fields = {}
        if self._station is None:
            fields.update({vol.Required(key): vol.All(str, vol.Strip, vol.Length(min=1)) for key in (
                CONF_EVSE_ID, CONF_LOCATION_ID, CONF_EVSE_UID, CONF_CONNECTOR_ID)})
        if self._card_id == MANUAL:
            fields[vol.Required(CONF_CARD_ID)] = vol.All(str, vol.Strip, vol.Length(min=1))
        return self.async_show_form(
            step_id="charger", data_schema=vol.Schema(fields),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return EFluxOptionsFlow()

    async def async_step_reauth(self, entry_data):
        """Start reauthentication."""
        return self.async_show_menu(
            step_id="reauth", menu_options=["reauth_confirm", "reauth_login"],
        )

    async def async_step_reauth_login(self, user_input=None):
        """Sign in again when the session has expired."""
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = EFluxClient(async_get_clientsession(self.hass))
            try:
                token = await client.login(
                    user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD]
                )
                await client.validate()
            except EFluxVerificationRequired:
                errors["base"] = "verification_required"
            except EFluxAuthError:
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={
                        CONF_SESSION_TOKEN: token,
                        CONF_DEVICE_TOKEN: client.device_token,
                        CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                    }
                )
        return self.async_show_form(
            step_id="reauth_login",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): PASSWORD,
            }),
            errors=errors,
        )

    async def async_step_reauth_confirm(self, user_input=None):
        """Replace a session after it has expired."""
        errors = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            try:
                raw_token, device_token = session_export(user_input[CONF_SESSION_TOKEN])
                client = EFluxClient(
                    async_get_clientsession(self.hass), raw_token, device_token,
                )
                await client.validate()
            except (EFluxAuthError, ValueError):
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={
                        CONF_SESSION_TOKEN: client.token,
                        CONF_DEVICE_TOKEN: client.device_token,
                    }
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): PASSWORD}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Replace the credentials of an existing entry without deleting it."""
        errors = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            try:
                raw_token, device_token = session_export(user_input[CONF_SESSION_TOKEN])
                client = EFluxClient(
                    async_get_clientsession(self.hass), raw_token, device_token,
                )
                await client.validate()
            except (EFluxAuthError, ValueError):
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_SESSION_TOKEN: client.token,
                        CONF_DEVICE_TOKEN: client.device_token,
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): PASSWORD}),
            errors=errors,
        )


class EFluxOptionsFlow(config_entries.OptionsFlow):
    """Change the card and connector without removing the account."""

    def __init__(self):
        self._cards = None
        self._stations = []
        self._selected = {}

    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if entry.data.get("pending_since") or entry.data.get("pending_stop") or (coordinator and coordinator._start_lock.locked()):
            return self.async_abort(reason="start_pending")
        current = {**entry.data, **entry.options}
        if self._cards is None:
            client = coordinator.client if coordinator else EFluxClient(
                async_get_clientsession(self.hass), entry.data[CONF_SESSION_TOKEN],
                entry.data.get(CONF_DEVICE_TOKEN),
            )
            if coordinator:
                try:
                    await coordinator.ensure_session()
                except EFluxError:
                    return self.async_abort(reason="authentication_expired")
            self._cards, self._stations = await discover(client)
        manual = "Handmatig invoeren" if self.hass.config.language == "nl" else "Manual entry"
        current_card = "Huidige laadpas" if self.hass.config.language == "nl" else "Current card"
        choices = {**self._cards, MANUAL: manual}
        if current.get(CONF_CARD_ID) not in choices:
            choices[current[CONF_CARD_ID]] = current_card
        stations_choices = {str(i): item["label"] for i, item in enumerate(self._stations)}
        stations_choices[MANUAL] = ("Handmatig / huidig laadpunt" if self.hass.config.language == "nl"
                                    else "Manual entry / current station")
        if user_input is not None:
            self._selected = {}
            if user_input[CONF_CARD_ID] != MANUAL:
                self._selected[CONF_CARD_ID] = user_input[CONF_CARD_ID]
            if user_input["station"] != MANUAL:
                station = self._stations[int(user_input["station"])]
                self._selected.update({key: station[key] for key in (
                    CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID, CONF_CONNECTOR_ID)})
            if len(self._selected) == 5:
                return self._save()
            return await self.async_step_manual()
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_CARD_ID, default=current[CONF_CARD_ID]): vol.In(choices),
            vol.Required("station", default=MANUAL): vol.In(stations_choices),
        }))

    async def async_step_manual(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._selected.update({key: value.strip() for key, value in user_input.items()})
            return self._save()
        fields = {vol.Required(key, default=current[key]): vol.All(str, vol.Strip, vol.Length(min=1))
                  for key in (CONF_CARD_ID, CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID, CONF_CONNECTOR_ID)
                  if key not in self._selected}
        return self.async_show_form(step_id="manual", data_schema=vol.Schema(fields))

    def _save(self):
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if self.config_entry.data.get("pending_since") or self.config_entry.data.get("pending_stop") or (coordinator and coordinator._start_lock.locked()):
            return self.async_abort(reason="start_pending")
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != self.config_entry.entry_id:
                other = {**entry.data, **entry.options}
                if other.get(CONF_EVSE_ID) == self._selected[CONF_EVSE_ID]:
                    return self.async_abort(reason="already_configured")
        return self.async_create_entry(title="", data={**self.config_entry.options, **self._selected})
