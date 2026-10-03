"""Base entity for the So Charged device."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SO_CHARGED_PAGE_URL
from .coordinator import SoEnergyCoordinator


class SoChargedEntity(CoordinatorEntity[SoEnergyCoordinator]):
    """Hangs every entity off one So Charged device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SoEnergyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name="So Charged",
            manufacturer="So Energy",
            model="So Charged",
            serial_number=entry.unique_id,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SO_CHARGED_PAGE_URL,
        )
