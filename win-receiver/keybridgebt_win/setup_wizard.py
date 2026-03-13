"""
First-run setup wizard for win-receiver.

Generates X25519 keypair, accepts the Mac's public key,
derives shared secret, confirms with a 6-digit fingerprint.

See docs/ARCHITECTURE.md §3.2 and docs/TASKS.md Task 16.
"""

import base64
import logging

log = logging.getLogger(__name__)


def run_setup() -> bool:
    """
    Interactive first-run setup.

    1. Generate keypair
    2. Prompt user to paste Mac's public key
    3. Display own public key for Mac
    4. Derive shared key, display fingerprint
    5. Store all keys in Windows Credential Manager

    Returns True on success.
    """
    from . import crypto, credential_store

    # 1. Generate keypair
    private_key, public_key = crypto.generate_keypair()
    pub_b64 = base64.b64encode(public_key).decode("ascii")

    print("\n" + "=" * 60)
    print("  keybridgeBT — First-Run Setup (Windows Receiver)")
    print("=" * 60)

    # 2. Accept Mac's public key
    print("\nPaste the Mac sender's public key (base64, from QR or copy):")
    peer_b64 = input("  > ").strip()

    try:
        peer_public_key = base64.b64decode(peer_b64)
        if len(peer_public_key) != 32:
            raise ValueError("Key must be 32 bytes")
    except Exception as e:
        print(f"\nInvalid key: {e}")
        return False

    # 3. Display own key
    print(f"\nYour public key (send this to the Mac):\n  {pub_b64}\n")

    # 4. Derive shared key and display fingerprint
    shared_key = crypto.derive_shared_key(private_key, peer_public_key)
    fingerprint = crypto.compute_fingerprint(shared_key)

    print(f"  🔑 Fingerprint: {fingerprint}")
    print("  Confirm this matches the number shown on the Mac.")

    confirm = input("\n  Match? (y/n): ").strip().lower()
    if confirm != "y":
        print("Setup cancelled.")
        return False

    # 5. Store keys
    credential_store.store_private_key(private_key)
    credential_store.store_public_key(public_key)
    credential_store.store_peer_public_key(peer_public_key)
    credential_store.store_shared_key(shared_key)

    print("\n  ✅ Setup complete! Keys stored in Windows Credential Manager.\n")
    return True
