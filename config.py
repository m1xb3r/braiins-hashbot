"""
Hashbot configuration loader.

Config lives in the data volume (CONFIG_FILE). On first boot it is created
from DEFAULTS — no example file needed. Values that come from the live Braiins
API (bid_id, speed_limit_ph) start empty and are filled in automatically after
the API key is set and the engine connects for the first time.
"""

import json
from pathlib import Path
from paths import CONFIG_FILE

PROJECT_ROOT     = Path(__file__).parent
CONFIG_PATH      = CONFIG_FILE
BRAIINS_BASE_URL = "https://hashpower.braiins.com/v1"

# Only the three settings the user meaningfully controls at startup.
# Everything else (bid_id, speed_limit_ph) is discovered from the live bid.
DEFAULTS: dict = {
    "bid_id":                "",    # auto-filled from active bid after connect
    "speed_limit_ph":        None,  # auto-filled from active bid after connect
    "dashboard_host":        "0.0.0.0",
    "dashboard_port":        8000,
    "poll_interval_seconds": 120,   # 2 minutes
    "topup_threshold_pct":   33,
    "timezone":              "Europe/Berlin",
}


def load_config() -> dict:
    """Load config from the data volume, creating it from defaults on first boot."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2))

    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        print(f"\n[hashbot] config.json is not valid JSON: {e}\n")
        raise SystemExit(1)

    cfg = {**DEFAULTS, **raw}

    if cfg.get("speed_limit_ph") not in (None, ""):
        cfg["speed_limit_ph"] = float(cfg["speed_limit_ph"])
    cfg["dashboard_port"]        = int(cfg["dashboard_port"])
    cfg["poll_interval_seconds"] = int(cfg["poll_interval_seconds"])
    cfg["topup_threshold_pct"]   = int(cfg["topup_threshold_pct"])
    return cfg


def save_config(updates: dict) -> dict:
    """Merge updates into config.json. API key fields are silently ignored."""
    updates.pop("api_key", None)
    updates.pop("BRAIINS_API_KEY", None)
    cfg = load_config()
    cfg.update(updates)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


# Module-level convenience exports
try:
    _cfg = load_config()
except SystemExit:
    _cfg = {**DEFAULTS}

BID_ID:                str        = _cfg.get("bid_id", "")
SPEED_LIMIT_PH:        float|None = _cfg.get("speed_limit_ph")
DASHBOARD_HOST:        str        = _cfg.get("dashboard_host", "0.0.0.0")
DASHBOARD_PORT:        int        = _cfg.get("dashboard_port", 8000)
POLL_INTERVAL_SECONDS: int        = _cfg.get("poll_interval_seconds", 120)
TOPUP_THRESHOLD_PCT:   int        = _cfg.get("topup_threshold_pct", 33)
TIMEZONE:              str        = _cfg.get("timezone", "Europe/Berlin")

from paths import EXPORT_DIR as _EXPORT_DIR
EXPORT_DIR: str = str(_EXPORT_DIR)
