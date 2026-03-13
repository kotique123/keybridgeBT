"""
Decryption module for win-receiver.

Uses PyNaCl:
  - X25519 keypair generation
  - Shared-key derivation via X25519 DH
  - crypto_secretstream_xchacha20poly1305 for streaming authenticated decryption

See docs/ARCHITECTURE.md §3 and docs/TASKS.md Task 4.
"""

import hashlib
import logging
from nacl.public import PrivateKey, PublicKey
from nacl.bindings import (
    crypto_box_beforenm,
    crypto_secretstream_xchacha20poly1305_init_pull,
    crypto_secretstream_xchacha20poly1305_pull,
    crypto_secretstream_xchacha20poly1305_KEYBYTES,
    crypto_secretstream_xchacha20poly1305_HEADERBYTES,
    crypto_secretstream_xchacha20poly1305_state,
)

log = logging.getLogger(__name__)

STREAM_HEADER_LEN = crypto_secretstream_xchacha20poly1305_HEADERBYTES


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate an X25519 keypair. Returns (private_key_bytes, public_key_bytes)."""
    sk = PrivateKey.generate()
    return bytes(sk), bytes(sk.public_key)


def derive_shared_key(private_key: bytes, peer_public_key: bytes) -> bytes:
    """X25519 DH → SHA-256 → 32-byte key suitable for crypto_secretstream."""
    sk = PrivateKey(private_key)
    pk = PublicKey(peer_public_key)
    # crypto_box_beforenm computes HSalsa20(X25519(sk,pk), 0) — stable 32-byte shared secret
    raw_shared = crypto_box_beforenm(pk.encode(), sk.encode())
    return hashlib.sha256(raw_shared).digest()


def compute_fingerprint(shared_key: bytes) -> str:
    """First 6 decimal digits of SHA-256(shared_key) — for visual confirmation."""
    h = hashlib.sha256(shared_key).digest()
    num = int.from_bytes(h[:4], "big") % 1_000_000
    return f"{num:06d}"


class StreamDecryptor:
    """
    Wraps crypto_secretstream pull (receiver) state for one session.
    Create a new instance for each BT connection, initialized with the header.
    """

    def __init__(self, shared_key: bytes, header: bytes):
        if len(shared_key) != crypto_secretstream_xchacha20poly1305_KEYBYTES:
            raise ValueError("shared_key must be 32 bytes")
        if len(header) != crypto_secretstream_xchacha20poly1305_HEADERBYTES:
            raise ValueError(f"header must be {crypto_secretstream_xchacha20poly1305_HEADERBYTES} bytes")
        self._state = crypto_secretstream_xchacha20poly1305_state()
        crypto_secretstream_xchacha20poly1305_init_pull(self._state, header, shared_key)

    def decrypt(self, ciphertext: bytes) -> bytes | None:
        """
        Decrypt one message.
        Returns plaintext on success, None on authentication failure.
        """
        try:
            plaintext, tag = crypto_secretstream_xchacha20poly1305_pull(
                self._state, ciphertext, ad=b""
            )
            return plaintext
        except Exception:
            log.warning("Decryption failed (authentication error)")
            return None
