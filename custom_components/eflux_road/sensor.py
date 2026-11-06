"""Status sensors for E-Flux by Road."""

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EVSE_ID, DOMAIN

SENSORS = (
    SensorEntityDescription(key="connector", name="Connectorstatus", icon="mdi:ev-station"),
    SensorEntityDescription(key="active", name="Actieve sessies", icon="mdi:ev-plug-type2"),
    SensorEntityDescription(key="remote", name="Laatste startstatus", icon="mdi:car-electric"),
    SensorEntityDescription(key="last_start", name="Laatste startverzoek", icon="mdi:history"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(EFluxSensor(coordinator, entry, description) for description in SENSORS)


class EFluxSensor(CoordinatorEntity, SensorEntity):
    """A status value from the E-Flux account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_EVSE_ID])},
            name="E-Flux laadpaal",
            manufacturer="E-Flux by Road",
        )

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.entity_description.key)

    @property
    def extra_state_attributes(self):
        if self.entity_description.key == "remote":
            reason = (self.coordinator.data or {}).get("remote_reason")
            if reason:
                return {"cancelled_reason": reason}
        return None
