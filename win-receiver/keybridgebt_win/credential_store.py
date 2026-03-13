"""
Windows Credential Manager storage via the keyring library.

Stores/retrieves X25519 keys and the derived shared secret.
All values are base64-encoded.

See docs/ARCHITECTURE.md §3.1 and docs/TASKS.md Task 6.
"""

import base64
import logging
import keyring

log = logging.getLogger(__name__)

SERVICE = "com.keybridgebt.receiver"

_KEY_PRIVATE = "private_key"
_KEY_PUBLIC = "public_key"
_KEY_PEER_PUBLIC = "peer_public_key"
_KEY_SHARED = "shared_key"


def _store(name: str, data: bytes) -> None:
    keyring.set_password(SERVICE, name, base64.b64encode(data).decode("ascii"))


def _load(name: str) -> bytes | None:
    val = keyring.get_password(SERVICE, name)
    if val is None:
        return None
    return base64.b64decode(val)


def store_private_key(key: bytes) -> None:
    _store(_KEY_PRIVATE, key)

def load_private_key() -> bytes | None:
    return _load(_KEY_PRIVATE)

def store_public_key(key: bytes) -> None:
    _store(_KEY_PUBLIC, key)

def load_public_key() -> bytes | None:
    return _load(_KEY_PUBLIC)

def store_peer_public_key(key: bytes) -> None:
    _store(_KEY_PEER_PUBLIC, key)

def load_peer_public_key() -> bytes | None:
    return _load(_KEY_PEER_PUBLIC)

def store_shared_key(key: bytes) -> None:
    _store(_KEY_SHARED, key)

def load_shared_key() -> bytes | None:
    return _load(_KEY_SHARED)

def has_completed_setup() -> bool:
    """True if all four keys are present in the credential store."""
    return all([
        load_private_key(),
        load_public_key(),
        load_peer_public_key(),
        load_shared_key(),
    ])
