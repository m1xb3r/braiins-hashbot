"""
Runtime path resolution for Hashbot.

All mutable data lives under DATA_DIR (default /data, override with
HASHBOT_DATA_DIR env var).  The API key env file can similarly be
overridden with HASHBOT_ENV_PATH.

In production (systemd) you can keep the old /var/lib/hashbot default by
setting HASHBOT_DATA_DIR=/var/lib/hashbot.  In Docker the compose file
mounts a named volume at /data and leaves everything else at the default.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Data directory
# ---------------------------------------------------------------------------

DATA_DIR: Path = Path(os.environ.get("HASHBOT_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Individual paths
# ---------------------------------------------------------------------------

CONFIG_FILE:        Path = DATA_DIR / "config.json"
PRICE_HISTORY_FILE: Path = DATA_DIR / "price_history.json"
ENGINE_STATE_FILE:  Path = DATA_DIR / "engine_state.json"
SETTINGS_FILE:      Path = DATA_DIR / "settings.json"
LOG_FILE:           Path = DATA_DIR / "hashbot.log"
EXPORT_DIR:         Path = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# API key — stored encrypted, never plaintext
MASTER_KEY_FILE: Path = DATA_DIR / "master.key"   # Fernet key, chmod 600
API_KEY_FILE:    Path = DATA_DIR / "api.key"       # Fernet-encrypted API key

# ---------------------------------------------------------------------------
# API key env file  (legacy bare-metal path, not used in Docker)
# ---------------------------------------------------------------------------

ENV_FILE: Path = Path(os.environ.get("HASHBOT_ENV_PATH", "/etc/hashbot/env"))
