"""So Charged actions: set the charge target, boost, cancel and reschedule."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import SoEnergyClient, SoEnergyError, build_intent
from .const import DOMAIN
from .coordinator import SoEnergyCoordinator

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENERGY_KWH = "energy_kwh"
ATTR_SOC_PERCENT = "soc_percent"
ATTR_READY_BY = "ready_by"

SERVICE_SET_CHARGE_TARGET = "set_charge_target"
SERVICE_START_BOOST = "start_boost"
SERVICE_STOP_BOOST = "stop_boost"
SERVICE_CANCEL_SCHEDULED_CHARGE = "cancel_scheduled_charge"
SERVICE_RESCHEDULE_CHARGE = "reschedule_charge"

_ENTRY_SCHEMA = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

SET_CHARGE_TARGET_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Exclusive(ATTR_ENERGY_KWH, "amount"): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Exclusive(ATTR_SOC_PERCENT, "amount"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_READY_BY): cv.time,
    }
)

# Simple one-shot controls: service name -> client method.
_CONTROLS: dict[str, Callable[[SoEnergyClient], Awaitable[None]]] = {
    SERVICE_START_BOOST: SoEnergyClient.async_start_boost,
    SERVICE_STOP_BOOST: SoEnergyClient.async_stop_boost,
    SERVICE_CANCEL_SCHEDULED_CHARGE: SoEnergyClient.async_cancel_scheduled_charge,
    SERVICE_RESCHEDULE_CHARGE: SoEnergyClient.async_reschedule,
}


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> SoEnergyCoordinator:
    """The account the call is for: the given entry, or the only one set up."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(f"No loaded So Energy account with entry ID {entry_id}")
    elif not entries:
        raise ServiceValidationError("So Energy is not set up")
    elif len(entries) > 1:
        raise ServiceValidationError(
            "More than one So Energy account is set up; pass config_entry_id"
        )
    return entries[0].runtime_data


def _next_occurrence(ready_by: time) -> datetime:
    """Next time the clock reads `ready_by` in HA's time zone, as the app does."""
    now = dt_util.now()
    candidate = datetime.combine(now.date(), ready_by, tzinfo=now.tzinfo)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), ready_by, tzinfo=now.tzinfo)
    return candidate


async def _async_set_charge_target(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(hass, call)
    client = coordinator.client
    current = coordinator.data.intent if coordinator.data else {}

    energy_kwh = call.data.get(ATTR_ENERGY_KWH)
    soc_percent = call.data.get(ATTR_SOC_PERCENT)
    if energy_kwh is None and soc_percent is None:
        # Keep the current amount when only the time changes.
        energy_kwh = current.get("energy_required_kwh")
        soc_percent = current.get("soc_required") if energy_kwh is None else None
    if energy_kwh is None and soc_percent is None:
        raise ServiceValidationError(
            "No current charge target to keep; give energy_kwh or soc_percent"
        )

    ready_by: time | None = call.data.get(ATTR_READY_BY)
    if ready_by is None:
        ready_by = dt_util.parse_time(current.get("ready_by_local_time") or "")
    if ready_by is None:
        raise ServiceValidationError("No current ready-by time to keep; give ready_by")

    intent = build_intent(_next_occurrence(ready_by), energy_kwh=energy_kwh, soc_percent=soc_percent)
    try:
        feasibility = await client.async_validate_intent(intent)
        await client.async_set_intent(intent)
    except SoEnergyError as err:
        raise HomeAssistantError(str(err)) from err
    await coordinator.async_request_refresh()
    return {"intent": intent, "feasibility": feasibility}


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the So Charged actions (once, for every account)."""

    async def set_charge_target(call: ServiceCall) -> ServiceResponse:
        return await _async_set_charge_target(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGE_TARGET,
        set_charge_target,
        schema=SET_CHARGE_TARGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    def make_control(method: Callable[[SoEnergyClient], Awaitable[None]]):
        async def control(call: ServiceCall) -> None:
            coordinator = _coordinator(hass, call)
            try:
                await method(coordinator.client)
            except SoEnergyError as err:
                raise HomeAssistantError(str(err)) from err
            await coordinator.async_request_refresh()

        return control

    for service, method in _CONTROLS.items():
        hass.services.async_register(
            DOMAIN, service, make_control(method), schema=vol.Schema(_ENTRY_SCHEMA)
        )
