"""Configuration flow for E-Flux by Road."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import EFluxAuthError, EFluxClient, EFluxError, EFluxVerificationRequired
from .const import (
    CONF_CARD_ID, CONF_CONNECTOR_ID, CONF_EVSE_ID, CONF_EVSE_UID, CONF_LOCATION_ID,
    CONF_SESSION_TOKEN, DEFAULT_CARD_ID, DEFAULT_CONNECTOR_ID, DEFAULT_EVSE_ID,
    DEFAULT_EVSE_UID, DEFAULT_LOCATION_ID, DOMAIN,
)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class EFluxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up an E-Flux account."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._username: str = ""

    async def async_step_user(self, user_input=None):
        """Choose how to authenticate."""
        return self.async_show_menu(
            step_id="user", menu_options=["login", "session"],
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
                self._username = user_input[CONF_USERNAME].strip()
                return await self.async_step_charger()
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
            client = EFluxClient(
                async_get_clientsession(self.hass), user_input[CONF_SESSION_TOKEN].strip()
            )
            try:
                await client.validate()
            except EFluxAuthError:
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                self._token = client.token
                return await self.async_step_charger()
        return self.async_show_form(
            step_id="session",
            data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): PASSWORD}),
            errors=errors,
        )

    async def async_step_charger(self, user_input=None):
        """Choose the charging station and the card."""
        if self._token is None:
            return self.async_abort(reason="authentication_expired")
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EVSE_ID].strip())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="E-Flux laadpaal",
                data={
                    **{key: value.strip() for key, value in user_input.items()},
                    CONF_SESSION_TOKEN: self._token,
                    CONF_USERNAME: self._username,
                },
            )
        return self.async_show_form(
            step_id="charger",
            data_schema=vol.Schema({
                vol.Required(CONF_EVSE_ID, default=DEFAULT_EVSE_ID): str,
                vol.Required(CONF_LOCATION_ID, default=DEFAULT_LOCATION_ID): str,
                vol.Required(CONF_EVSE_UID, default=DEFAULT_EVSE_UID): str,
                vol.Required(CONF_CONNECTOR_ID, default=DEFAULT_CONNECTOR_ID): str,
                vol.Required(CONF_CARD_ID, default=DEFAULT_CARD_ID): str,
            }),
        )

    async def async_step_reauth(self, entry_data):
        """Start reauthentication."""
        return self.async_show_menu(
            step_id="reauth", menu_options=["reauth_login", "reauth_confirm"],
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
            client = EFluxClient(
                async_get_clientsession(self.hass), user_input[CONF_SESSION_TOKEN].strip()
            )
            try:
                await client.validate()
            except EFluxAuthError:
                errors["base"] = "invalid_auth"
            except EFluxError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_SESSION_TOKEN: client.token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): PASSWORD}),
            errors=errors,
        )
