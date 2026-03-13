"""
First-run setup wizard for mac-sender.

Generates X25519 keypair, displays public key as QR code,
accepts the Windows public key, derives shared secret, and
confirms with a 6-digit fingerprint.

See docs/ARCHITECTURE.md §3.2 and docs/TASKS.md Task 15.
"""

import sys
import base64
import logging
import subprocess
import tempfile

log = logging.getLogger(__name__)


def run_setup() -> bool:
    """
    Interactive first-run setup.

    1. Generate keypair
    2. Display public key as QR code
    3. Prompt user to paste Windows public key
    4. Derive shared key, display fingerprint
    5. Store all keys in macOS Keychain

    Returns True on success.
    """
    from . import crypto, keychain

    # 1. Generate keypair
    private_key, public_key = crypto.generate_keypair()
    pub_b64 = base64.b64encode(public_key).decode("ascii")

    print("\n" + "=" * 60)
    print("  keybridgeBT — First-Run Setup (Mac Sender)")
    print("=" * 60)
    print(f"\nYour public key:\n  {pub_b64}\n")

    # 2. Display as QR code
    try:
        import qrcode
        qr = qrcode.make(pub_b64)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        qr.save(tmp.name)
        tmp.close()
        print(f"QR code saved to: {tmp.name}")
        # Use subprocess to avoid shell injection from untrusted file paths
        subprocess.run(["open", tmp.name], check=False)
    except ImportError:
        print("(Install 'qrcode[pil]' to display a QR code)")

    # 3. Accept Windows public key
    print("\nPaste the Windows receiver's public key (base64):")
    peer_b64 = input("  > ").strip()

    try:
        peer_public_key = base64.b64decode(peer_b64)
        if len(peer_public_key) != 32:
            raise ValueError("Key must be 32 bytes")
    except Exception as e:
        print(f"\nInvalid key: {e}")
        return False

    # 4. Derive shared key and display fingerprint
    shared_key = crypto.derive_shared_key(private_key, peer_public_key)
    fingerprint = crypto.compute_fingerprint(shared_key)

    print(f"\n  🔑 Fingerprint: {fingerprint}")
    print("  Confirm this matches the number shown on the Windows machine.")

    confirm = input("\n  Match? (y/n): ").strip().lower()
    if confirm != "y":
        print("Setup cancelled.")
        return False

    # 5. Store keys
    keychain.store_private_key(private_key)
    keychain.store_public_key(public_key)
    keychain.store_peer_public_key(peer_public_key)
    keychain.store_shared_key(shared_key)

    print("\n  ✅ Setup complete! Keys stored in macOS Keychain.\n")
    return True
