# So Energy (So Charged) for Home Assistant

Tracks your **So Energy So Charged** EV allowance in Home Assistant: how much of your kWh / miles allowance you've used, what's left, your last charge and your charger's smart-charging state.

> Unofficial. So Energy has no public API. This integration signs in the same way the So Energy website does, so it may break if So Energy or its smart-charging partner (Axle Energy) change their site.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/jpjoseph12/ha-so-energy`, category **Integration**.
2. Find **So Energy** in HACS and download it.
3. Restart Home Assistant.
4. Settings → Devices & services → **Add integration** → **So Energy**, then sign in with your So Energy email and password.

## Entities

All entities hang off one **So Charged** device.

| Entity | What it shows |
|---|---|
| Remaining energy | kWh left in your So Charged allowance |
| Energy used | kWh used this period (works with long-term statistics) |
| Energy allowance | Total kWh in the current allowance |
| Allowance used | % of the allowance used |
| Remaining miles / Miles used | The same allowance, in miles as shown in the app |
| Rewards balance | So Charged rewards balance (£) |
| Last charge | kWh of the most recent charge session; attributes list recent sessions |
| Charger status | Charger power state (e.g. `unplugged`, `charging`); attributes include plugged-in, charge rate and reachability |
| Smart charge stage | Smart-charging stage; attributes include the schedule window and target |
| Last updated | When data was last fetched (diagnostic) |

## Settings and details

- **Update interval**: the integration's **Configure** button. 30 minutes by default (5 minutes to 24 hours).
- **Account**: the device page shows your So Energy account number as the serial number and links to the So Charged page.
- **Diagnostics**: on the integration's ⋮ menu choose **Download diagnostics** for a redacted dump of the latest data and the last error.
- **Password changes**: if So Energy rejects the saved password, Home Assistant asks you to re-enter it.

## How it works

1. Signs in to So Energy's customer portal (`portal-api-gateway-v2.so.energy`). It keeps a 1-hour access token and renews it with the 30-day refresh cookie, only logging in again when that runs out.
2. Gets a So Charged token for your account and uses it to open a session with the smart-charging app (`app.smart-charging.so.energy`).
3. Reads the app's home page data, which is where the allowance, sessions and charger state come from.

Your email and password are stored in Home Assistant's config entry, like any other cloud integration, and are only sent to So Energy.

## Licence

[MIT](LICENSE)
