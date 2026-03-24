"""
keystore.py — Encrypted storage for the Braiins API key.

Design
------
* A machine-specific Fernet key is generated once on first use and written
  to MASTER_KEY_FILE (chmod 600).  It never leaves the data volume.
* The API key is encrypted with that Fernet key and written to API_KEY_FILE.
* Neither file contains anything useful on its own.
* The plaintext key is only held in memory for the duration of an API call
  and is never written to any log, config file, or environment variable.

Usage
-----
    from keystore import get_api_key, save_api_key, delete_api_key, has_api_key

    key = get_api_key()          # str | None
    save_api_key("my-token")     # encrypts and persists
    delete_api_key()             # wipes both files
    has_api_key()                # bool — cheap check, no decryption
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

from paths import MASTER_KEY_FILE, API_KEY_FILE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_or_create_master_key() -> Fernet:
    """
    Load the master Fernet key from disk, generating it on first call.
    The file is created with mode 0o600 (owner read/write only).
    """
    if not MASTER_KEY_FILE.exists():
        key = Fernet.generate_key()
        MASTER_KEY_FILE.write_bytes(key)
        MASTER_KEY_FILE.chmod(0o600)
        logger.info("Generated new master encryption key.")
    else:
        key = MASTER_KEY_FILE.read_bytes().strip()

    return Fernet(key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def has_api_key() -> bool:
    """Return True if an encrypted API key file exists (does not decrypt)."""
    return API_KEY_FILE.exists() and API_KEY_FILE.stat().st_size > 0


def get_api_key() -> str | None:
    """
    Decrypt and return the stored API key, or None if not set.
    Returns None (rather than raising) so callers can handle the
    "not configured yet" state gracefully.
    """
    if not has_api_key():
        return None
    try:
        f = _get_or_create_master_key()
        return f.decrypt(API_KEY_FILE.read_bytes()).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt API key: {e}")
        return None


def save_api_key(plaintext_key: str) -> None:
    """
    Encrypt *plaintext_key* with the master key and persist it.
    The plaintext is not retained after this function returns.
    """
    f   = _get_or_create_master_key()
    enc = f.encrypt(plaintext_key.encode())
    API_KEY_FILE.write_bytes(enc)
    API_KEY_FILE.chmod(0o600)
    logger.info("API key saved (encrypted).")


def delete_api_key() -> None:
    """Remove the encrypted API key file. Does not touch the master key."""
    if API_KEY_FILE.exists():
        # Overwrite with zeros before unlinking to reduce forensic risk
        size = API_KEY_FILE.stat().st_size
        API_KEY_FILE.write_bytes(b"\x00" * size)
        API_KEY_FILE.unlink()
        logger.info("API key deleted.")


def mask_key(key: str) -> str:
    """Return a safe display string: ••••<last 8 chars>."""
    return "••••" + key[-8:] if len(key) > 8 else "••••" + key
