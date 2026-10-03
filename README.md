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
| Smart charge stage | Smart-charging stage; attributes include the schedule window, the charge target (`target_energy_kwh` / `target_soc_percent`, `ready_by`) and `target_feasibility` |
| Last updated | When data was last fetched (diagnostic) |
| Boost charge | Button: start charging now, ignoring the schedule |
| Cancel boost | Button: stop a boost |
| Cancel scheduled charge | Button: stop the current smart-charge schedule |
| Reschedule charge | Button: build a new schedule after a cancel or stopped boost |

## Actions

The buttons are also available as actions, along with **Set charge target** (the app's *Update target*):

```yaml
# 30 kWh by 06:30 (charger only, no vehicle linked)
action: so_energy.set_charge_target
data:
  energy_kwh: 30
  ready_by: "06:30"
response_variable: result   # optional: result.feasibility is "achievable", "impossible", ...
```

- Use `soc_percent` instead of `energy_kwh` if you have linked a vehicle in So Charged.
- Leave out a field to keep its current value. For example, `ready_by` on its own changes only the time.
- `ready_by` is the next time that clock time comes round, in Home Assistant's time zone, as in the app.
- The others take no data: `so_energy.start_boost`, `so_energy.stop_boost`, `so_energy.cancel_scheduled_charge` and `so_energy.reschedule_charge`.
- If you have more than one So Energy account set up, pass `config_entry_id`.

## Settings and details

- **Update interval**: the integration's **Configure** button. 30 minutes by default (5 minutes to 24 hours).
- **Account**: the device page shows your So Energy account number as the serial number and links to the So Charged page.
- **Diagnostics**: on the integration's ⋮ menu choose **Download diagnostics** for a redacted dump of the latest data and the last error.
- **Password changes**: if So Energy rejects the saved password, Home Assistant asks you to re-enter it.

## How it works

1. Signs in to So Energy's customer portal (`portal-api-gateway-v2.so.energy`). It keeps a 1-hour access token and renews it with the 30-day refresh cookie, only logging in again when that runs out.
2. Gets a So Charged token for your account and uses it to open a session with the smart-charging app (`app.smart-charging.so.energy`).
3. Reads the app's home page data, which is where the allowance, sessions and charger state come from.
4. That data also includes a short-lived API token and your charger's asset ID. The app's buttons use these to call Axle's API (`api.axle.energy`) directly, and the integration's controls make the same calls:

| Control | Request |
|---|---|
| Read target | `GET /components/asset/{asset}/intent` |
| Check target | `POST /components/asset/{asset}/intent/validate` `{"intent": {...}}` |
| Set target | `POST /components/asset/{asset}/event/intent` `{"intent": {"ready_by", "charging_mode", "energy_required_kwh" or "soc_required"}}` |
| Boost / cancel boost | `POST /components/asset/{asset}/enode/charge-now` / `charge-now-deleted` |
| Cancel scheduled charge | `POST /components/asset/{asset}/enode/scheduled-charge-deleted` |
| Reschedule | `POST /components/asset/{asset}/reschedule` |

Your email and password are stored in Home Assistant's config entry, like any other cloud integration, and are only sent to So Energy.

## Licence

[MIT](LICENSE)
