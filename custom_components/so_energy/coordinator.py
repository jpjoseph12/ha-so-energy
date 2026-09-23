"""Polling coordinator for So Energy."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SoChargedData, SoEnergyAuthError, SoEnergyClient, SoEnergyError
from .const import CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SoEnergyCoordinator(DataUpdateCoordinator[SoChargedData]):
    """Fetch So Charged data on the configured interval (30 minutes by default)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SoEnergyClient) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client

    async def _async_update_data(self) -> SoChargedData:
        try:
            return await self.client.async_get_data()
        except SoEnergyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SoEnergyError as err:
            raise UpdateFailed(str(err)) from err
