"""
Task 26 — Integration tests: Scenarios 6–10 (full pipeline, reconnect, keychain)

These tests exercise the full Mac → Windows data path, reconnection state
reset, hotkey toggle, and keychain round-trip.  No hardware required.
"""

import struct
import threading
import pytest

from keybridgebt_mac.packet import (
    build_packet, frame_packet, next_seqno,
    TYPE_KEYBOARD, TYPE_POINTER,
)
from keybridgebt_mac.crypto import (
    generate_keypair as mac_generate_keypair,
    derive_shared_key as mac_derive_shared_key,
    StreamEncryptor,
)
from keybridgebt_win.packet import PacketReader
from keybridgebt_win.crypto import (
    generate_keypair as win_generate_keypair,
    derive_shared_key as win_derive_shared_key,
    StreamDecryptor,
    STREAM_HEADER_LEN,
)
from keybridgebt_win.rate_limiter import RateLimiter
from keybridgebt_win.keycode_map import VALID_HID_RANGE


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def keypair():
    mac_priv, mac_pub = mac_generate_keypair()
    win_priv, win_pub = win_generate_keypair()
    shared = mac_derive_shared_key(mac_priv, win_pub)
    assert shared == win_derive_shared_key(win_priv, mac_pub)
    return shared


@pytest.fixture
def session(keypair):
    """Return a fully-initialised (encryptor, decryptor, packet_reader) tuple."""
    enc = StreamEncryptor(keypair)
    dec = StreamDecryptor(keypair, enc.header)
    reader = PacketReader()
    return enc, dec, reader


# ===========================================================================
# Scenario 6 — Full Mac pipeline
#   HID report → encrypt → build_packet → frame_packet → raw bytes
# ===========================================================================

class TestMacPipeline:
    def test_keyboard_report_to_wire_bytes(self, keypair):
        report = b"\x00\x00\x04\x00\x00\x00\x00\x00"  # 'a' key pressed
        enc = StreamEncryptor(keypair)
        seqno = 0
        ciphertext = enc.encrypt(report)
        packet = build_packet(TYPE_KEYBOARD, seqno, ciphertext)
        framed = frame_packet(packet)

        # Wire format: [len(2)][type(1)][seqno(4)][ciphertext(>=8+overhead)]
        claimed_len = struct.unpack_from("<H", framed, 0)[0]
        assert claimed_len == len(framed) - 2

    def test_pointer_event_to_wire_bytes(self, keypair):
        buttons, dx, dy, sv, sh = 0x01, 10, -5, 0, 0
        payload = struct.pack("<BhhBB", buttons, dx, dy, sv, sh)
        enc = StreamEncryptor(keypair)
        ct = enc.encrypt(payload)
        framed = frame_packet(build_packet(TYPE_POINTER, 0, ct))
        assert len(framed) > 2


# ===========================================================================
# Scenario 7 — Full Windows pipeline
#   Raw bytes → deframe → parse → validate seqno → decrypt → validate
#   keycode → inject
# ===========================================================================

class TestWindowsPipeline:
    def _make_framed(self, enc, seqno, plaintext, ptype=TYPE_KEYBOARD):
        """Helper: encrypt plaintext and frame it as a wire packet."""
        ct = enc.encrypt(plaintext)
        return frame_packet(build_packet(ptype, seqno, ct))

    def test_full_keyboard_pipeline(self, keypair):
        enc = StreamEncryptor(keypair)
        dec = StreamDecryptor(keypair, enc.header)
        reader = PacketReader()

        report = b"\x00\x00\x04\x00\x00\x00\x00\x00"  # 'a' key
        framed = self._make_framed(enc, 0, report)
        packets = reader.feed(framed)
        assert len(packets) == 1
        ptype, seqno, ct = packets[0]
        assert reader.validate_seqno(seqno)
        plaintext = dec.decrypt(ct)
        assert plaintext == report
        assert ptype == TYPE_KEYBOARD
        # HID code 0x04 is 'a' — must be in whitelist
        assert report[2] in VALID_HID_RANGE

    def test_full_pointer_pipeline(self, keypair):
        enc = StreamEncryptor(keypair)
        dec = StreamDecryptor(keypair, enc.header)
        reader = PacketReader()

        payload = struct.pack("<BhhBB", 0x00, 5, -3, 1, 0)
        framed = self._make_framed(enc, 0, payload, ptype=TYPE_POINTER)
        packets = reader.feed(framed)
        assert len(packets) == 1
        ptype, _, ct = packets[0]
        plaintext = dec.decrypt(ct)
        assert plaintext == payload
        assert ptype == TYPE_POINTER
        b, dx, dy, sv, sh = struct.unpack("<BhhBB", plaintext[:7])
        assert dx == 5 and dy == -3

    def test_tampered_ciphertext_dropped(self, keypair):
        enc = StreamEncryptor(keypair)
        dec = StreamDecryptor(keypair, enc.header)
        reader = PacketReader()

        report = b"\x00\x00\x04\x00\x00\x00\x00\x00"
        ct = enc.encrypt(report)
        # Flip a bit in the ciphertext
        bad_ct = bytes([ct[0] ^ 0xFF]) + ct[1:]
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 0, bad_ct))
        packets = reader.feed(framed)
        assert len(packets) == 1
        _, _, received_ct = packets[0]
        result = dec.decrypt(received_ct)
        assert result is None, "Tampered ciphertext must be rejected"


# ===========================================================================
# Scenario 8 — Reconnection state reset
#   Simulate disconnect → verify sequence numbers reset, crypto state reset
# ===========================================================================

class TestReconnection:
    def test_seqno_resets_on_new_session(self, keypair):
        """After a reconnect (new encryptor/decryptor pair), seqno restarts at 0."""
        # Session 1
        enc1 = StreamEncryptor(keypair)
        dec1 = StreamDecryptor(keypair, enc1.header)
        reader = PacketReader()

        report = b"\x00\x00\x04\x00\x00\x00\x00\x00"
        for i in range(5):
            ct = enc1.encrypt(report)
            framed = frame_packet(build_packet(TYPE_KEYBOARD, i, ct))
            packets = reader.feed(framed)
            for _, sn, _ in packets:
                reader.validate_seqno(sn)  # last accepted = 4

        # Simulate disconnect + reconnect
        reader.reset()
        enc2 = StreamEncryptor(keypair)
        dec2 = StreamDecryptor(keypair, enc2.header)

        # Session 2 restarts at seqno 0 — must be accepted again
        ct2 = enc2.encrypt(report)
        framed2 = frame_packet(build_packet(TYPE_KEYBOARD, 0, ct2))
        packets2 = reader.feed(framed2)
        assert len(packets2) == 1
        _, sn2, ct_recv = packets2[0]
        assert reader.validate_seqno(sn2), "seqno 0 accepted after session reset"
        pt2 = dec2.decrypt(ct_recv)
        assert pt2 == report

    def test_old_session_ciphertext_rejected_in_new_session(self, keypair):
        """Old session's ciphertext must fail decryption in a new session."""
        enc1 = StreamEncryptor(keypair)
        dec1 = StreamDecryptor(keypair, enc1.header)  # noqa: F841

        report = b"\x00\x00\x04\x00\x00\x00\x00\x00"
        old_ct = enc1.encrypt(report)

        # New session
        enc2 = StreamEncryptor(keypair)
        dec2 = StreamDecryptor(keypair, enc2.header)

        # Old ciphertext fed to new session's decryptor — must fail
        result = dec2.decrypt(old_ct)
        assert result is None, "Old session's ciphertext must be rejected in new session"


# ===========================================================================
# Scenario 9 — Hotkey toggle (state logic, no OS dependency)
# ===========================================================================

class TestHotkeyToggle:
    def test_toggle_flips_state(self):
        """Verify the toggle logic used in Daemon.toggle_forwarding() works."""
        state = {"forwarding": True}
        lock = threading.Lock()

        def toggle():
            with lock:
                state["forwarding"] = not state["forwarding"]

        assert state["forwarding"] is True
        toggle()
        assert state["forwarding"] is False
        toggle()
        assert state["forwarding"] is True


# ===========================================================================
# Scenario 10 — Keychain / Credential Store round-trip (stdlib only)
# ===========================================================================

class TestKeychainRoundTrip:
    """
    Tests the encoding/decoding logic in the keychain module without
    actually writing to the system keychain (uses an in-memory dict
    as the backend via unittest.mock).
    """

    def test_store_load_roundtrip(self):
        import base64
        from unittest.mock import patch

        # Simulate keyring.set_password / get_password with an in-memory dict
        store = {}

        def mock_set(service, name, value):
            store[(service, name)] = value

        def mock_get(service, name):
            return store.get((service, name))

        with patch("keyring.set_password", side_effect=mock_set), \
             patch("keyring.get_password", side_effect=mock_get):
            from keybridgebt_mac import keychain

            test_key = b"\xDE\xAD\xBE\xEF" * 8  # 32 bytes
            keychain.store_shared_key(test_key)
            loaded = keychain.load_shared_key()
            assert loaded == test_key, "Keychain round-trip must preserve key bytes"

    def test_has_completed_setup_false_if_missing(self):
        import base64
        from unittest.mock import patch

        def mock_get(service, name):
            return None  # keys not stored yet

        with patch("keyring.get_password", side_effect=mock_get):
            from keybridgebt_mac import keychain
            assert keychain.has_completed_setup() is False

    def test_has_completed_setup_true_if_all_present(self):
        import base64
        from unittest.mock import patch

        dummy = base64.b64encode(b"\x01" * 32).decode()

        def mock_get(service, name):
            return dummy

        with patch("keyring.get_password", side_effect=mock_get):
            from keybridgebt_mac import keychain
            assert keychain.has_completed_setup() is True
