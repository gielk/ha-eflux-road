"""Status sensors for E-Flux by Road."""

from datetime import datetime
import math

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EVSE_ID, DOMAIN

SENSORS = (
    SensorEntityDescription(key="connector", name="Connectorstatus", icon="mdi:ev-station"),
    SensorEntityDescription(key="active", name="Actieve sessies", icon="mdi:ev-plug-type2"),
    SensorEntityDescription(key="remote", name="Laatste startstatus", icon="mdi:car-electric"),
    SensorEntityDescription(key="remote_connector", name="Laatste sessiestatus gekozen laadpunt", icon="mdi:ev-station"),
    SensorEntityDescription(key="remote_connector_raw", name="Startstatus gekozen laadpunt volgens E-Flux", icon="mdi:cloud-outline"),
    SensorEntityDescription(key="last_successful_update", name="E-Flux succesvol bijgewerkt", device_class=SensorDeviceClass.TIMESTAMP),
    SensorEntityDescription(key="last_start", name="Laatste startverzoek", icon="mdi:history"),
    SensorEntityDescription(key="session_energy", name="Energie huidige sessie", device_class=SensorDeviceClass.ENERGY,
                            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR),
    SensorEntityDescription(key="session_started", name="Sessie gestart", device_class=SensorDeviceClass.TIMESTAMP),
    SensorEntityDescription(key="charging_state", name="Laadstatus sessie", icon="mdi:ev-station"),
    SensorEntityDescription(key="card", name="Geselecteerde laadpas", icon="mdi:credit-card"),
    SensorEntityDescription(key="card_status", name="Status laadpas", icon="mdi:credit-card-check"),
    SensorEntityDescription(key="max_power", name="Maximaal laadvermogen", device_class=SensorDeviceClass.POWER,
                            native_unit_of_measurement=UnitOfPower.KILO_WATT),
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
            identifiers={(DOMAIN, entry.options.get(CONF_EVSE_ID, entry.data[CONF_EVSE_ID]))},
            name="E-Flux laadpaal",
            manufacturer="E-Flux by Road",
        )

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get(self.entity_description.key)
        if self.entity_description.key in {"session_energy", "max_power"}:
            return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                             and math.isfinite(value) and value >= 0) else None
        if self.entity_description.key in {"session_started", "last_successful_update"}:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo is not None else None
            except (ValueError, AttributeError):
                return None
        return value

    @property
    def extra_state_attributes(self):
        if self.entity_description.key == "connector":
            return {key: (self.coordinator.data or {}).get(key) for key in
                    ("location_name", "location_address", "location_city")}
        if self.entity_description.key == "remote":
            reason = (self.coordinator.data or {}).get("remote_reason")
            return {"cancelled_reason": reason}
        return None
