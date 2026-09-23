"""Constants for the So Energy integration."""
DOMAIN = "so_energy"

CONF_ACCOUNT_ID = "account_id"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_SCAN_INTERVAL_MINUTES = 30
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 1440

PORTAL_URL = "https://portal-api-gateway-v2.so.energy"
WEBSITE_URL = "https://www.so.energy"
AXLE_APP_URL = "https://app.smart-charging.so.energy"
SO_CHARGED_PAGE_URL = f"{WEBSITE_URL}/myaccount/so-charged/app"
