"""Client for the So Energy portal and the So Charged (Axle) smart-charging app.

There is no public API. This mirrors what www.so.energy does in the browser:

1. So Energy portal: log in (or refresh via the HttpOnly refresh cookie) to get
   a short-lived Bearer access token.
2. Exchange that for an Axle "component token" for the account.
3. Post the component token to the Axle app (app.smart-charging.so.energy) to
   get an app session cookie, then read the home route's loader data, which
   carries the So Charged allowance, charge sessions and charger state.

The Axle app is a React Router app, so its `.data` responses are encoded with
turbo-stream; `decode_turbo_stream` flattens them back into plain Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
from typing import Any

import aiohttp

from .const import AXLE_APP_URL, PORTAL_URL, SO_CHARGED_PAGE_URL, WEBSITE_URL

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HOME_DATA_PATH = "/app/_.data?_routes=routes%2Fhome%2F_layout%2Croutes%2Fhome%2Findex"
HOME_ROUTE = "routes/home/index"


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
        return route["data"]

    async def async_get_data(self) -> SoChargedData:
        """One poll: reuse the Axle session if it's still good, otherwise renew it."""
        home = await self._async_fetch_home()
        if home is None:
            await self._async_start_axle_session()
            home = await self._async_fetch_home()
            if home is None:
                raise SoEnergyError("So Charged app did not return account data after signing in")

        assets = home.get("connectedAssets") or {}
        return SoChargedData(
            home=home,
            widget=home.get("accountWidgetPromise") or {},
            sessions=home.get("chargeSessionsPromise") or [],
            chargers=assets.get("chargers") or [],
            site_state=home.get("siteState") or {},
        )
