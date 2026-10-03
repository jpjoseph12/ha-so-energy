"""Client for the So Energy portal and the So Charged (Axle) smart-charging app.

There is no public API. This mirrors what www.so.energy does in the browser:

1. So Energy portal: log in (or refresh via the HttpOnly refresh cookie) to get
   a short-lived Bearer access token.
2. Exchange that for an Axle "component token" for the account.
3. Post the component token to the Axle app (app.smart-charging.so.energy) to
   get an app session cookie, then read the home route's loader data, which
   carries the So Charged allowance, charge sessions and charger state.
4. The home layout's loader data also carries a ~24h Bearer JWT and the
   charger/vehicle asset IDs. The app's buttons (boost, update target,
   reschedule) call the Axle REST API (api.axle.energy) directly with them.

The Axle app is a React Router app, so its `.data` responses are encoded with
turbo-stream; `decode_turbo_stream` flattens them back into plain Python.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
from typing import Any

import aiohttp

from .const import AXLE_API_URL, AXLE_APP_URL, PORTAL_URL, SO_CHARGED_PAGE_URL, WEBSITE_URL

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HOME_DATA_PATH = "/app/_.data?_routes=routes%2Fhome%2F_layout%2Croutes%2Fhome%2Findex"
HOME_ROUTE = "routes/home/index"
HOME_LAYOUT_ROUTE = "routes/home/_layout"
# The only mode the app's "Update target" page sends.
CHARGING_MODE = "minimise_charging_time"


class SoEnergyError(Exception):
    """Generic So Energy failure (network, unexpected response)."""


class SoEnergyAuthError(SoEnergyError):
    """Credentials were rejected."""


@dataclass
class SoChargedData:
    """What one poll returns."""

    home: dict[str, Any]
    widget: dict[str, Any] = field(default_factory=dict)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    chargers: list[dict[str, Any]] = field(default_factory=list)
    site_state: dict[str, Any] = field(default_factory=dict)
    intent: dict[str, Any] = field(default_factory=dict)
    intent_feasibility: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# --- turbo-stream ------------------------------------------------------------

_SPECIAL = {
    -1: None,  # hole
    -2: math.nan,
    -3: -math.inf,
    -4: -0.0,
    -5: None,  # null
    -6: math.inf,
    -7: None,  # undefined
}
_PROMISE_LINE = re.compile(r"^([PE])(\d+):(.*)$")


def decode_turbo_stream(text: str) -> Any:
    """Decode a React Router single-fetch (turbo-stream) payload.

    Line 1 is a flat array of values; objects are {"_<key index>": <value index>}
    and tagged arrays like ["D", ms] or ["P", id] encode dates and promises.
    Each later "P<id>:<json>" line resolves a promise: an array payload is
    appended to the value table (the resolved value is its first element), a
    number payload is a direct index.
    """
    lines = [line for line in text.split("\n") if line]
    if not lines:
        raise SoEnergyError("Empty response from So Charged app")
    values: list[Any] = json.loads(lines[0])
    if not isinstance(values, list):
        raise SoEnergyError("Unexpected So Charged response format")

    promises: dict[int, tuple[str, int]] = {}
    for line in lines[1:]:
        match = _PROMISE_LINE.match(line)
        if not match:
            continue
        kind, pid, payload = match.group(1), int(match.group(2)), json.loads(match.group(3))
        if isinstance(payload, list):
            promises[pid] = (kind, len(values))
            values.extend(payload)
        elif isinstance(payload, int):
            promises[pid] = (kind, payload)

    cache: dict[int, Any] = {}

    def hydrate(index: int) -> Any:
        if index < 0:
            return _SPECIAL.get(index)
        if index in cache:
            return cache[index]
        value = values[index]

        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            cache[index] = obj
            for key, ref in value.items():
                obj[values[int(key[1:])]] = hydrate(ref)
            return obj

        if isinstance(value, list):
            if value and isinstance(value[0], str):
                out = _hydrate_tagged(value, hydrate, promises)
                cache[index] = out
                return out
            arr: list[Any] = []
            cache[index] = arr
            arr.extend(hydrate(ref) for ref in value)
            return arr

        return value

    return hydrate(0)


def _hydrate_tagged(value: list, hydrate, promises: dict[int, tuple[str, int]]) -> Any:
    tag = value[0]
    if tag == "D":
        return datetime.fromtimestamp(value[1] / 1000, tz=timezone.utc)
    if tag == "P":
        kind, ref = promises.get(value[1], ("E", -7))
        return hydrate(ref) if kind == "P" else None
    if tag == "S":
        return [hydrate(ref) for ref in value[1:]]
    if tag == "M":
        return {hydrate(value[i]): hydrate(value[i + 1]) for i in range(1, len(value) - 1, 2)}
    if tag == "Z":
        return hydrate(value[1]) if len(value) > 1 else {}
    return None


def _find_key(obj: Any, key: str) -> Any:
    """Depth-first search for the first value stored under `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_key(child, key)
        if found is not None:
            return found
    return None


def _jwt_expiry(token: str) -> datetime:
    """Expiry of a JWT (unverified); assume ~1 hour if it can't be read."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    except (IndexError, KeyError, TypeError, ValueError):
        return datetime.now(timezone.utc) + timedelta(hours=1)


def build_intent(
    ready_by: datetime,
    energy_kwh: float | None = None,
    soc_percent: int | None = None,
) -> dict[str, Any]:
    """Charge target as the app's "Update target" page sends it.

    `ready_by` must be timezone-aware; the app sends the next occurrence of the
    chosen local time. Give `energy_kwh` for a charger-only setup or
    `soc_percent` when a vehicle is linked.
    """
    if (energy_kwh is None) == (soc_percent is None):
        raise ValueError("Give exactly one of energy_kwh or soc_percent")
    intent: dict[str, Any] = {
        "ready_by": ready_by.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "charging_mode": CHARGING_MODE,
    }
    if energy_kwh is not None:
        intent["energy_required_kwh"] = energy_kwh
    else:
        intent["soc_required"] = soc_percent
    return intent


# --- client ------------------------------------------------------------------


class SoEnergyClient:
    """Keeps the portal and Axle sessions alive across polls."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        account_id: int | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self.account_id = account_id
        self._access_token: str | None = None
        self._access_expiry = datetime.min.replace(tzinfo=timezone.utc)
        self._api_token: str | None = None
        self._api_expiry = datetime.min.replace(tzinfo=timezone.utc)
        self._asset_ids: dict[str, str] = {}

    # portal ------------------------------------------------------------------

    def _portal_headers(self, auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": WEBSITE_URL,
            "Referer": f"{WEBSITE_URL}/",
            "User-Agent": USER_AGENT,
        }
        if auth and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        token = payload.get("accessToken")
        if not token:
            raise SoEnergyError("So Energy did not return an access token")
        self._access_token = token
        expiry = payload.get("accessTokenExpiry")
        try:
            self._access_expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            self._access_expiry = datetime.now(timezone.utc) + timedelta(minutes=50)

    async def async_login(self) -> None:
        """Full email/password login; also sets the refresh cookie."""
        try:
            async with self._session.post(
                f"{PORTAL_URL}/api/v1/auth/login",
                json={"email": self._email, "password": self._password, "extendedSession": True},
                headers=self._portal_headers(auth=False),
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise SoEnergyAuthError("So Energy rejected the email or password")
                resp.raise_for_status()
                self._store_tokens(await resp.json(content_type=None))
        except aiohttp.ClientError as err:
            raise SoEnergyError(f"So Energy login failed: {err}") from err

    async def _async_refresh(self) -> bool:
        """Refresh via the refresh cookie. Returns False if a full login is needed."""
        try:
            async with self._session.post(
                f"{PORTAL_URL}/api/v1/auth/refresh",
                json={"extendedSession": True},
                headers=self._portal_headers(auth=False),
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    return False
                self._store_tokens(await resp.json(content_type=None))
                return True
        except (aiohttp.ClientError, SoEnergyError) as err:
            _LOGGER.debug("So Energy token refresh failed, will log in again: %s", err)
            return False

    async def _async_ensure_access_token(self) -> None:
        if self._access_token and datetime.now(timezone.utc) < self._access_expiry - timedelta(minutes=2):
            return
        if self._access_token and await self._async_refresh():
            return
        await self.async_login()

    async def _async_portal_get(self, path: str) -> Any:
        await self._async_ensure_access_token()
        for attempt in range(2):
            try:
                async with self._session.get(
                    f"{PORTAL_URL}{path}", headers=self._portal_headers(), timeout=TIMEOUT
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        await self.async_login()
                        continue
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise SoEnergyError(f"So Energy request {path} failed: {err}") from err
        raise SoEnergyAuthError("So Energy session could not be re-established")

    async def async_get_account_id(self) -> int:
        """Look up the account ID (the last one the customer used in the portal)."""
        me = await self._async_portal_get("/api/v1/customers/me")
        account_id = me.get("accountIDLastVisited") or next(iter(me.get("accountIDs") or []), None)
        if account_id is None:
            raise SoEnergyError("No So Energy account found for this login")
        self.account_id = int(account_id)
        return self.account_id

    # Axle / So Charged -------------------------------------------------------

    async def _async_axle_get(self, path: str, referer: str) -> str:
        async with self._session.get(
            f"{AXLE_APP_URL}{path}",
            headers={"User-Agent": USER_AGENT, "Referer": referer, "Accept": "*/*"},
            timeout=TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            return await resp.text()

    async def _async_start_axle_session(self) -> None:
        """Swap a fresh component token for an Axle app session cookie."""
        if self.account_id is None:
            await self.async_get_account_id()
        token_payload = await self._async_portal_get(f"/api/v1/accounts/{self.account_id}/axle/token")
        component_token = token_payload.get("accessToken")
        if not component_token:
            raise SoEnergyError("So Energy did not return a So Charged token")

        try:
            csrf = _find_key(
                decode_turbo_stream(await self._async_axle_get("/auth/token.data", f"{WEBSITE_URL}/")),
                "csrfToken",
            )
            if not csrf:
                raise SoEnergyError("So Charged app did not return a CSRF token")

            async with self._session.post(
                f"{AXLE_APP_URL}/auth/token.data",
                data={
                    "token": component_token,
                    "enodeRedirectUrl": SO_CHARGED_PAGE_URL,
                    "_csrf": csrf,
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Origin": AXLE_APP_URL,
                    "Referer": f"{AXLE_APP_URL}/auth/token",
                },
                timeout=TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                result = decode_turbo_stream(await resp.text())
        except aiohttp.ClientError as err:
            raise SoEnergyError(f"So Charged sign-in failed: {err}") from err

        if isinstance(result, dict) and result.get("error"):
            raise SoEnergyError(f"So Charged sign-in failed: {result['error']}")

    async def _async_fetch_home(self) -> dict[str, Any] | None:
        """Home route loader data, or None if the Axle session is missing/expired."""
        try:
            decoded = decode_turbo_stream(
                await self._async_axle_get(HOME_DATA_PATH, f"{AXLE_APP_URL}/app/")
            )
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                return None
            raise SoEnergyError(f"So Charged request failed: {err}") from err
        except aiohttp.ClientError as err:
            raise SoEnergyError(f"So Charged request failed: {err}") from err

        route = decoded.get(HOME_ROUTE) if isinstance(decoded, dict) else None
        if not isinstance(route, dict) or not isinstance(route.get("data"), dict):
            # A redirect back to /auth/... means the app session has lapsed.
            return None

        layout = (decoded.get(HOME_LAYOUT_ROUTE) or {}).get("data") or {}
        if token := layout.get("token"):
            self._api_token = token
            self._api_expiry = _jwt_expiry(token)
        if isinstance(layout.get("assetIdMap"), dict):
            self._asset_ids = {k: v for k, v in layout["assetIdMap"].items() if v}
        return route["data"]

    async def _async_load_home(self) -> dict[str, Any]:
        """Home data, signing in to the Axle app first if its session has lapsed."""
        home = await self._async_fetch_home()
        if home is None:
            await self._async_start_axle_session()
            home = await self._async_fetch_home()
            if home is None:
                raise SoEnergyError("So Charged app did not return account data after signing in")
        return home

    async def async_get_data(self) -> SoChargedData:
        """One poll: reuse the Axle session if it's still good, otherwise renew it."""
        home = await self._async_load_home()

        intent: dict[str, Any] = {}
        if self._asset_ids:
            try:
                intent = (await self.async_get_intent()).get("intent") or {}
            except SoEnergyError as err:
                _LOGGER.debug("Could not read So Charged charge target: %s", err)

        assets = home.get("connectedAssets") or {}
        feasibility = home.get("intentFeasibilityPromise")
        return SoChargedData(
            home=home,
            widget=home.get("accountWidgetPromise") or {},
            sessions=home.get("chargeSessionsPromise") or [],
            chargers=assets.get("chargers") or [],
            site_state=home.get("siteState") or {},
            intent=intent,
            intent_feasibility=feasibility if isinstance(feasibility, str) else None,
        )

    # Axle REST API (smart-charging controls) ---------------------------------

    @property
    def has_vehicle(self) -> bool:
        """True when a vehicle is linked, so targets are % state of charge, not kWh."""
        return "vehicle" in self._asset_ids

    def _asset_id_for(self, purpose: str) -> str:
        """Same choice the app makes: targets go to the vehicle, controls to the charger."""
        first, second = ("vehicle", "charger") if purpose == "intent" else ("charger", "vehicle")
        asset_id = self._asset_ids.get(first) or self._asset_ids.get(second)
        if not asset_id:
            raise SoEnergyError("No So Charged charger or vehicle is linked to this account")
        return asset_id

    async def _async_ensure_api_token(self) -> None:
        if self._api_token and datetime.now(timezone.utc) < self._api_expiry - timedelta(minutes=5):
            return
        await self._async_load_home()
        if not self._api_token:
            raise SoEnergyError("So Charged app did not return an API token")

    async def _async_api(
        self, method: str, purpose: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        """Call api.axle.energy for the right asset, renewing the token once on a 401."""
        await self._async_ensure_api_token()
        url = f"{AXLE_API_URL}/components/asset/{self._asset_id_for(purpose)}{path}"
        for attempt in range(2):
            try:
                async with self._session.request(
                    method,
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._api_token}",
                        "Accept": "application/json",
                        "Origin": WEBSITE_URL,
                        "Referer": f"{AXLE_APP_URL}/",
                        "User-Agent": USER_AGENT,
                    },
                    timeout=TIMEOUT,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        self._api_token = None
                        await self._async_ensure_api_token()
                        continue
                    text = await resp.text()
                    if resp.status >= 400:
                        try:
                            detail = json.loads(text).get("detail") or text
                        except (ValueError, AttributeError):
                            detail = text
                        raise SoEnergyError(f"So Charged rejected {method} {path}: {resp.status} {detail}")
                    return json.loads(text) if text else None
            except aiohttp.ClientError as err:
                raise SoEnergyError(f"So Charged request {path} failed: {err}") from err
        raise SoEnergyError("So Charged API token could not be renewed")

    async def async_get_intent(self) -> dict[str, Any]:
        """Current charge target: {"intent": {energy_required_kwh, soc_required, ready_by, ...}}."""
        return await self._async_api("GET", "intent", "/intent") or {}

    async def async_validate_intent(self, intent: dict[str, Any]) -> str | None:
        """Check a target without saving it: "achievable", "impossible", "plug_in_too_late", ..."""
        result = await self._async_api("POST", "intent", "/intent/validate", {"intent": intent})
        return (result or {}).get("feasibility")

    async def async_set_intent(self, intent: dict[str, Any]) -> None:
        """Save a new charge target (the app's "Update target")."""
        await self._async_api("POST", "intent", "/event/intent", {"intent": intent})

    async def async_start_boost(self) -> None:
        """Charge now, ignoring the schedule (the app's "Boost charge")."""
        await self._async_api("POST", "control", "/enode/charge-now")

    async def async_stop_boost(self) -> None:
        """Cancel a boost (the app's "Cancel boost")."""
        await self._async_api("POST", "control", "/enode/charge-now-deleted")

    async def async_cancel_scheduled_charge(self) -> None:
        """Stop tonight's smart-charge schedule (the app's "Cancel" while charging on schedule)."""
        await self._async_api("POST", "control", "/enode/scheduled-charge-deleted")

    async def async_reschedule(self) -> None:
        """Generate a new smart-charge schedule (the app's "Reschedule")."""
        await self._async_api("POST", "control", "/reschedule")
