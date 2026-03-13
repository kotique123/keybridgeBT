"""
Encryption module for mac-sender.

Uses PyNaCl:
  - X25519 keypair generation
  - Shared-key derivation via X25519 DH
  - crypto_secretstream_xchacha20poly1305 for streaming authenticated encryption

See docs/ARCHITECTURE.md §3 for security architecture.
"""

import hashlib
from nacl.public import PrivateKey, PublicKey
from nacl.bindings import (
    crypto_box_beforenm,
    crypto_secretstream_xchacha20poly1305_init_push,
    crypto_secretstream_xchacha20poly1305_push,
    crypto_secretstream_xchacha20poly1305_TAG_MESSAGE,
    crypto_secretstream_xchacha20poly1305_KEYBYTES,
    crypto_secretstream_xchacha20poly1305_HEADERBYTES,
    crypto_secretstream_xchacha20poly1305_state,
)


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate an X25519 keypair. Returns (private_key_bytes, public_key_bytes)."""
    sk = PrivateKey.generate()
    return bytes(sk), bytes(sk.public_key)


def derive_shared_key(private_key: bytes, peer_public_key: bytes) -> bytes:
    """
    X25519 DH → SHA-256 → 32-byte key suitable for crypto_secretstream.
    Uses crypto_box_beforenm which is the stable PyNaCl binding for DH.
    """
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


class StreamEncryptor:
    """
    Wraps crypto_secretstream push (sender) state for one session.
    Create a new instance for each BT connection.
    """

    def __init__(self, shared_key: bytes):
        if len(shared_key) != crypto_secretstream_xchacha20poly1305_KEYBYTES:
            raise ValueError("shared_key must be 32 bytes")
        self._state = crypto_secretstream_xchacha20poly1305_state()
        self._header = crypto_secretstream_xchacha20poly1305_init_push(self._state, shared_key)

    @property
    def header(self) -> bytes:
        """24-byte stream header — send to receiver once at session start."""
        return self._header

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt one message. Returns ciphertext (plaintext + 17 bytes overhead)."""
        return crypto_secretstream_xchacha20poly1305_push(
            self._state,
            plaintext,
            ad=b"",
            tag=crypto_secretstream_xchacha20poly1305_TAG_MESSAGE,
        )
