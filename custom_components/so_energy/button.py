"""So Charged buttons: the app's boost / cancel / reschedule controls."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SoEnergyConfigEntry
from .api import SoEnergyClient, SoEnergyError
from .coordinator import SoEnergyCoordinator
from .entity import SoChargedEntity


@dataclass(frozen=True, kw_only=True)
class SoChargedButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[SoEnergyClient], Awaitable[None]]


BUTTONS: tuple[SoChargedButtonDescription, ...] = (
    SoChargedButtonDescription(
        key="start_boost",
        translation_key="start_boost",
        icon="mdi:lightning-bolt",
        press_fn=SoEnergyClient.async_start_boost,
    ),
    SoChargedButtonDescription(
        key="stop_boost",
        translation_key="stop_boost",
        icon="mdi:lightning-bolt-outline",
        press_fn=SoEnergyClient.async_stop_boost,
    ),
    SoChargedButtonDescription(
        key="cancel_scheduled_charge",
        translation_key="cancel_scheduled_charge",
        icon="mdi:calendar-remove",
        press_fn=SoEnergyClient.async_cancel_scheduled_charge,
    ),
    SoChargedButtonDescription(
        key="reschedule_charge",
        translation_key="reschedule_charge",
        icon="mdi:calendar-refresh",
        press_fn=SoEnergyClient.async_reschedule,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SoEnergyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SoChargedButton(coordinator, description) for description in BUTTONS)


class SoChargedButton(SoChargedEntity, ButtonEntity):
    """One smart-charging control from the So Charged app."""

    entity_description: SoChargedButtonDescription

    def __init__(self, coordinator: SoEnergyCoordinator, description: SoChargedButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except SoEnergyError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
