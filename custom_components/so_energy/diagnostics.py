"""Diagnostics for So Energy (Settings → Devices & Services → So Energy → ⋮ → Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SoEnergyConfigEntry

TO_REDACT = {
    "email",
    "password",
    "token",
    "accessToken",
    "externalUserId",
    "device_id",
    "identifier",
    "asset_id",
    "assetIdMap",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SoEnergyConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "update_interval": str(coordinator.update_interval),
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        },
        "data": async_redact_data(
            {
                "fetched_at": data.fetched_at.isoformat() if data else None,
                "account_widget": data.widget if data else None,
                "site_state": data.site_state if data else None,
                "intent": data.intent if data else None,
                "intent_feasibility": data.intent_feasibility if data else None,
                "chargers": data.chargers if data else None,
                "charge_sessions": data.sessions if data else None,
            },
            TO_REDACT,
        ),
    }
