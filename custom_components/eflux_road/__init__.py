"""E-Flux by Road integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EFluxClient
from .const import CONF_SESSION_TOKEN, DOMAIN
from .coordinator import EFluxCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the E-Flux by Road domain services."""
    async def async_handle_start_session(call: ServiceCall) -> None:
        """Handle starting a charge session on configured chargers."""
        coordinators = hass.data.get(DOMAIN, {})
        if not coordinators:
            raise HomeAssistantError("Geen E-Flux laadpaal geconfigureerd")
        for coordinator in coordinators.values():
            await coordinator.start_until_confirmed()
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "start_session", async_handle_start_session)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured charger."""
    client = EFluxClient(async_get_clientsession(hass), entry.data[CONF_SESSION_TOKEN])
    coordinator = EFluxCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the charger."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
