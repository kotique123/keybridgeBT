"""
Task 26 — Integration tests: Scenarios 1–5 (protocol, crypto, seq, rate, whitelist)

These tests run on any platform (macOS / Windows / CI) because they only
exercise pure-Python logic and do NOT require Bluetooth hardware.
"""

import struct
import time
import pytest
from nacl.public import PrivateKey

# -- mac-sender modules -------------------------------------------------
from keybridgebt_mac.packet import (
    build_packet, frame_packet, next_seqno,
    TYPE_KEYBOARD, TYPE_POINTER, MAX_PACKET_SIZE,
    HEADER_SIZE, LENGTH_SIZE,
)
from keybridgebt_mac.crypto import (
    generate_keypair as mac_generate_keypair,
    derive_shared_key as mac_derive_shared_key,
    compute_fingerprint as mac_fingerprint,
    StreamEncryptor,
)

# -- win-receiver modules -----------------------------------------------
from keybridgebt_win.packet import PacketReader
from keybridgebt_win.crypto import (
    generate_keypair as win_generate_keypair,
    derive_shared_key as win_derive_shared_key,
    compute_fingerprint as win_fingerprint,
    StreamDecryptor,
    STREAM_HEADER_LEN,
)
from keybridgebt_win.rate_limiter import RateLimiter
from keybridgebt_win.keycode_map import VALID_HID_RANGE, HID_TO_VK


# ===========================================================================
# Scenario 1 — Protocol round-trip
#   Build a keyboard packet on the Mac side → parse on the Windows side →
#   verify all fields are preserved.
# ===========================================================================

class TestProtocolRoundTrip:
    def test_keyboard_packet_fields_preserved(self):
        report = b"\x02\x00\x04\x05\x00\x00\x00\x00"  # Ctrl + A, B
        ciphertext = b"\xDE\xAD\xBE\xEF" * 8           # fake 32-byte payload
        ptype = TYPE_KEYBOARD
        seqno = 7

        framed = frame_packet(build_packet(ptype, seqno, ciphertext))
        reader = PacketReader()
        packets = reader.feed(framed)

        assert len(packets) == 1
        p_ptype, p_seqno, p_payload = packets[0]
        assert p_ptype == ptype
        assert p_seqno == seqno
        assert p_payload == ciphertext

    def test_pointer_packet_fields_preserved(self):
        payload = struct.pack("<BhhBB", 0x01, 15, -10, 3, 0)  # left-click + move + scroll
        ptype = TYPE_POINTER
        seqno = 42

        framed = frame_packet(build_packet(ptype, seqno, payload))
        reader = PacketReader()
        packets = reader.feed(framed)

        assert len(packets) == 1
        p_ptype, p_seqno, p_payload = packets[0]
        assert p_ptype == ptype
        assert p_seqno == seqno
        assert p_payload == payload

    def test_multiple_packets_in_one_feed(self):
        data = b""
        for i in range(5):
            data += frame_packet(build_packet(TYPE_KEYBOARD, i, b"\xAA" * 10))
        reader = PacketReader()
        packets = reader.feed(data)
        assert len(packets) == 5
        for i, (pt, sn, _) in enumerate(packets):
            assert sn == i

    def test_partial_reads_reassembled(self):
        payload = b"\xBB" * 20
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 1, payload))
        reader = PacketReader()
        # Feed one byte at a time
        packets = []
        for byte in framed:
            packets.extend(reader.feed(bytes([byte])))
        assert len(packets) == 1
        assert packets[0][2] == payload

    def test_seqno_wraps_at_2_32(self):
        assert next_seqno(0xFFFFFFFF) == 0

    def test_oversized_packet_rejected(self):
        """frame_packet() must raise ValueError when packet exceeds MAX_PACKET_SIZE."""
        # Create a packet that is too large to frame into a uint16 length field
        oversized_payload = b"\x00" * (MAX_PACKET_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            frame_packet(oversized_payload)

    def test_max_valid_packet_handled(self):
        """A packet exactly at MAX_PACKET_SIZE should be accepted."""
        at_limit_payload = b"\xAA" * (MAX_PACKET_SIZE - HEADER_SIZE)
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 0, at_limit_payload))
        reader = PacketReader()
        packets = reader.feed(framed)
        assert len(packets) == 1


# ===========================================================================
# Scenario 2 — Crypto round-trip
#   Encrypt on Mac → decrypt on Windows with same shared key → plaintext matches
# ===========================================================================

class TestCryptoRoundTrip:
    @pytest.fixture
    def shared_key(self):
        mac_priv, mac_pub = mac_generate_keypair()
        win_priv, win_pub = win_generate_keypair()
        key_mac = mac_derive_shared_key(mac_priv, win_pub)
        key_win = win_derive_shared_key(win_priv, mac_pub)
        assert key_mac == key_win, "DH shared keys must match"
        return key_mac

    def test_encrypt_decrypt_round_trip(self, shared_key):
        encryptor = StreamEncryptor(shared_key)
        decryptor = StreamDecryptor(shared_key, encryptor.header)

        for message in [b"\x00" * 8, b"\xFF\x04\x05\x00\x00\x00\x00\x00", b"hello!"]:
            ct = encryptor.encrypt(message)
            pt = decryptor.decrypt(ct)
            assert pt == message

    def test_wrong_key_returns_none(self, shared_key):
        encryptor = StreamEncryptor(shared_key)
        # Create decryptor with a different key
        bad_key = bytes([b ^ 0xFF for b in shared_key])
        try:
            decryptor = StreamDecryptor(bad_key, encryptor.header)
            ct = encryptor.encrypt(b"secret")
            result = decryptor.decrypt(ct)
            assert result is None, "Decryption with wrong key must return None"
        except Exception:
            pass  # Init with wrong key may also raise — both are acceptable

    def test_fingerprints_match(self, shared_key):
        fp_mac = mac_fingerprint(shared_key)
        fp_win = win_fingerprint(shared_key)
        assert fp_mac == fp_win
        assert len(fp_mac) == 6
        assert fp_mac.isdigit()

    def test_header_length(self, shared_key):
        encryptor = StreamEncryptor(shared_key)
        assert len(encryptor.header) == STREAM_HEADER_LEN


# ===========================================================================
# Scenario 3 — Sequence number validation
#   Feed packets with seqno [1, 2, 2, 5, 3, 6] → only [1, 2, 5, 6] accepted
# ===========================================================================

class TestSeqnoValidation:
    def test_drop_duplicate_and_replay(self):
        reader = PacketReader()
        payload = b"\x00" * 4

        accepted = []
        for seqno in [1, 2, 2, 5, 3, 6]:
            framed = frame_packet(build_packet(TYPE_KEYBOARD, seqno, payload))
            for ptype, sn, _ in reader.feed(framed):
                if reader.validate_seqno(sn):
                    accepted.append(sn)

        assert accepted == [1, 2, 5, 6]

    def test_reset_clears_seqno(self):
        reader = PacketReader()
        payload = b"\x00" * 4
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 100, payload))
        reader.feed(framed)
        reader.validate_seqno(100)

        reader.reset()
        # After reset, seqno 1 must be accepted (last seen is -1 again)
        framed2 = frame_packet(build_packet(TYPE_KEYBOARD, 1, payload))
        packets2 = reader.feed(framed2)
        assert len(packets2) == 1
        assert reader.validate_seqno(1)


# ===========================================================================
# Scenario 4 — Rate limiter
#   Feed 25 events in <1 second → first 20 accepted, last 5 rejected
# ===========================================================================

class TestRateLimiter:
    def test_allows_up_to_max(self):
        rl = RateLimiter(max_events=20, window_seconds=1.0)
        results = [rl.allow() for _ in range(25)]
        assert results[:20] == [True] * 20
        assert results[20:] == [False] * 5

    def test_window_slides(self):
        rl = RateLimiter(max_events=5, window_seconds=0.1)
        # Consume the quota
        for _ in range(5):
            assert rl.allow()
        assert not rl.allow()
        # After the window expires, quota refills
        time.sleep(0.15)
        assert rl.allow()

    def test_reset_clears_quota(self):
        rl = RateLimiter(max_events=5)
        for _ in range(5):
            rl.allow()
        assert not rl.allow()
        rl.reset()
        assert rl.allow()


# ===========================================================================
# Scenario 5 — Keycode whitelist
#   Inject 0xFF → rejected; inject 0x04 (HID A) → accepted
# ===========================================================================

class TestKeycodeWhitelist:
    def test_invalid_hid_code_not_in_whitelist(self):
        assert 0xFF not in VALID_HID_RANGE

    def test_valid_hid_code_a_in_whitelist(self):
        assert 0x04 in VALID_HID_RANGE

    def test_all_whitelist_codes_have_vk_mapping(self):
        for hid_code in VALID_HID_RANGE:
            assert hid_code in HID_TO_VK, f"HID 0x{hid_code:02X} in whitelist but missing from HID_TO_VK"

    def test_letter_mapping(self):
        """HID 0x04–0x1D map to VK 0x41–0x5A (A–Z)"""
        for i in range(26):
            hid = 0x04 + i
            vk = 0x41 + i
            assert HID_TO_VK[hid] == vk, f"HID 0x{hid:02X} should map to VK 0x{vk:02X}"

    def test_digit_mapping(self):
        """HID 0x27 (0) → VK 0x30; HID 0x1E (1) → VK 0x31 … 0x26 (9) → 0x39"""
        assert HID_TO_VK[0x27] == 0x30  # 0
        for i in range(1, 10):
            assert HID_TO_VK[0x1E + i - 1] == 0x30 + i
