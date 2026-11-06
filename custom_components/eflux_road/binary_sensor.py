"""Map availability reported by E-Flux, which may be delayed."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EVSE_ID, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EFluxAvailability(hass.data[DOMAIN][entry.entry_id], entry)])


class EFluxAvailability(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Beschikbaar volgens E-Flux"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_available"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.options.get(CONF_EVSE_ID, entry.data[CONF_EVSE_ID]))},
            name="E-Flux laadpaal", manufacturer="E-Flux by Road")

    @property
    def is_on(self):
        state = (self.coordinator.data or {}).get("connector")
        if state == "AVAILABLE":
            return True
        if state in {"PREPARING", "CHARGING", "OCCUPIED", "FAULTED", "UNAVAILABLE", "RESERVED"}:
            return False
        return None
