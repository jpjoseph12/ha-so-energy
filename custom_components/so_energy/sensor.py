"""So Charged sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SoEnergyConfigEntry
from .api import SoChargedData
from .const import DOMAIN, SO_CHARGED_PAGE_URL
from .coordinator import SoEnergyCoordinator


def _range(data: SoChargedData) -> dict[str, Any]:
    return data.widget.get("range") or {}


def _num(value: Any) -> float | None:
    return round(float(value), 3) if isinstance(value, (int, float)) else None


def _remaining(data: SoChargedData, total_key: str, used_key: str) -> float | None:
    total, used = _num(_range(data).get(total_key)), _num(_range(data).get(used_key))
    if total is None or used is None:
        return None
    return round(max(total - used, 0.0), 3)


def _percent_used(data: SoChargedData) -> float | None:
    total, used = _num(_range(data).get("kwhAvailable")), _num(_range(data).get("kwhUsed"))
    if not total or used is None:
        return None
    return round(used / total * 100, 1)


def _rewards_balance(data: SoChargedData) -> float | None:
    pence = (data.widget.get("rewards") or {}).get("currentBalancePence")
    return round(pence / 100, 2) if isinstance(pence, (int, float)) else None


def _latest_session(data: SoChargedData) -> dict[str, Any]:
    sessions = [s for s in data.sessions if isinstance(s, dict)]
    return max(sessions, key=lambda s: s.get("start_timestamp") or "", default={})


def _session_attrs(data: SoChargedData) -> dict[str, Any]:
    latest = _latest_session(data)
    return {
        "start": latest.get("start_timestamp"),
        "end": latest.get("end_timestamp"),
        "cost_pence": latest.get("cost_pence"),
        "recent_sessions": [
            {
                "start": s.get("start_timestamp"),
                "end": s.get("end_timestamp"),
                "energy_kwh": s.get("energy_kwh"),
            }
            for s in sorted(data.sessions, key=lambda s: s.get("start_timestamp") or "", reverse=True)
            if isinstance(s, dict)
        ],
    }


def _charger(data: SoChargedData) -> dict[str, Any]:
    return data.chargers[0] if data.chargers else {}


def _charger_attrs(data: SoChargedData) -> dict[str, Any]:
    charger = _charger(data)
    state = charger.get("charge_state") or {}
    return {
        "charger": " ".join(filter(None, [charger.get("brand"), charger.get("model")])) or None,
        "plugged_in": state.get("is_plugged_in"),
        "charging": state.get("is_charging"),
        "charge_rate_kw": state.get("charge_rate_kw"),
        "max_current_amps": state.get("max_current_amps"),
        "reachable": charger.get("is_reachable"),
        "last_seen": charger.get("last_seen"),
    }


def _schedule_attrs(data: SoChargedData) -> dict[str, Any]:
    schedule = data.site_state.get("schedule") or {}
    return {
        "schedule_start": schedule.get("schedule_start"),
        "schedule_end": schedule.get("schedule_end"),
        "target_amount": schedule.get("target_amount"),
        "target_unit": schedule.get("target_unit"),
        "failed_action": data.site_state.get("failed_action"),
    }


def _lower(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) else None


@dataclass(frozen=True, kw_only=True)
class SoChargedSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SoChargedData], Any]
    attrs_fn: Callable[[SoChargedData], dict[str, Any]] | None = None


SENSORS: tuple[SoChargedSensorDescription, ...] = (
    SoChargedSensorDescription(
        key="kwh_remaining",
        translation_key="kwh_remaining",
        icon="mdi:battery-charging-outline",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _remaining(d, "kwhAvailable", "kwhUsed"),
    ),
    SoChargedSensorDescription(
        key="kwh_used",
        translation_key="kwh_used",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _num(_range(d).get("kwhUsed")),
    ),
    SoChargedSensorDescription(
        key="kwh_allowance",
        translation_key="kwh_allowance",
        icon="mdi:ev-station",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _num(_range(d).get("kwhAvailable")),
    ),
    SoChargedSensorDescription(
        key="percent_used",
        translation_key="percent_used",
        icon="mdi:percent-outline",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_percent_used,
    ),
    SoChargedSensorDescription(
        key="miles_remaining",
        translation_key="miles_remaining",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _remaining(d, "rangeAvailable", "rangeUsed"),
    ),
    SoChargedSensorDescription(
        key="miles_used",
        translation_key="miles_used",
        icon="mdi:car-electric",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _num(_range(d).get("rangeUsed")),
    ),
    SoChargedSensorDescription(
        key="rewards_balance",
        translation_key="rewards_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="GBP",
        suggested_display_precision=2,
        value_fn=_rewards_balance,
    ),
    SoChargedSensorDescription(
        key="last_charge_energy",
        translation_key="last_charge_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: _num(_latest_session(d).get("energy_kwh")),
        attrs_fn=_session_attrs,
    ),
    SoChargedSensorDescription(
        key="charger_status",
        translation_key="charger_status",
        icon="mdi:ev-plug-type2",
        value_fn=lambda d: _lower((_charger(d).get("charge_state") or {}).get("power_delivery_state")),
        attrs_fn=_charger_attrs,
    ),
    SoChargedSensorDescription(
        key="smart_charge_stage",
        translation_key="smart_charge_stage",
        icon="mdi:calendar-clock",
        value_fn=lambda d: _lower(d.site_state.get("stage")),
        attrs_fn=_schedule_attrs,
    ),
    SoChargedSensorDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.fetched_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SoEnergyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SoChargedSensor(coordinator, description) for description in SENSORS)


class SoChargedSensor(CoordinatorEntity[SoEnergyCoordinator], SensorEntity):
    """One value from the So Charged account widget / home data."""

    _attr_has_entity_name = True
    entity_description: SoChargedSensorDescription

    def __init__(self, coordinator: SoEnergyCoordinator, description: SoChargedSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name="So Charged",
            manufacturer="So Energy",
            model="So Charged",
            serial_number=entry.unique_id,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SO_CHARGED_PAGE_URL,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
