"""E-Flux session start button."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EVSE_ID, DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EFluxStartButton(coordinator, entry)])


class EFluxStartButton(CoordinatorEntity, ButtonEntity):
    """Request one charge session using the configured charge card."""

    _attr_has_entity_name = True
    _attr_name = "Start laadsessie"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_start_session"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_EVSE_ID])},
            name="E-Flux laadpaal",
            manufacturer="E-Flux by Road",
        )

    async def async_press(self) -> None:
        await self.coordinator.start_until_confirmed()
        await self.coordinator.async_request_refresh()
