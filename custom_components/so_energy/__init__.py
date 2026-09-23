"""The So Energy integration (So Charged allowance)."""
from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import SoEnergyClient
from .const import CONF_ACCOUNT_ID
from .coordinator import SoEnergyCoordinator

PLATFORMS = [Platform.SENSOR]

type SoEnergyConfigEntry = ConfigEntry[SoEnergyCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SoEnergyConfigEntry) -> bool:
    """Set up So Energy from a config entry."""
    # Own cookie jar: the portal refresh cookie and the So Charged app session
    # cookie must not leak into (or be clobbered by) HA's shared session.
    session = async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
    client = SoEnergyClient(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_ACCOUNT_ID),
    )
    coordinator = SoEnergyCoordinator(hass, entry, client)
    entry.async_on_unload(session.close)

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SoEnergyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
