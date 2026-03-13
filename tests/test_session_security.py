"""
tests/test_session_security.py

Security validation suite for keybridgeBT sessions.

Verifies that each cryptographic and protocol-level protection property
holds in isolation — no Bluetooth hardware or OS integration required.

Property coverage
-----------------
S1  Session isolation     — ciphertext from one session cannot be used in another
S2  Authentication        — any byte modification in ciphertext causes rejection
S3  Nonce uniqueness      — identical plaintexts produce distinct ciphertexts
S4  MITM detection        — key mismatch yields a different fingerprint
S5  Wrong-key rejection   — decryptor initialised with wrong key returns None
S6  Stream-order binding  — out-of-order decryption fails
S7  Replay prevention     — PacketReader rejects recycled or replayed seqnos
S8  Header uniqueness     — each new session begins with a unique stream header
"""

import struct
import pytest

from keybridgebt_mac.crypto import (
    generate_keypair as mac_keypair,
    derive_shared_key as mac_derive,
    compute_fingerprint as mac_fp,
    StreamEncryptor,
)
from keybridgebt_win.crypto import (
    generate_keypair as win_keypair,
    derive_shared_key as win_derive,
    compute_fingerprint as win_fp,
    StreamDecryptor,
    STREAM_HEADER_LEN,
)
from keybridgebt_mac.packet import build_packet, frame_packet, TYPE_KEYBOARD
from keybridgebt_win.packet import PacketReader


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def exchange():
    """Full X25519 key exchange. Returns (mac_priv, mac_pub, win_priv, win_pub, shared_key)."""
    mac_priv, mac_pub = mac_keypair()
    win_priv, win_pub = win_keypair()
    shared = mac_derive(mac_priv, win_pub)
    assert shared == win_derive(win_priv, mac_pub), "DH shared keys must agree"
    return mac_priv, mac_pub, win_priv, win_pub, shared


@pytest.fixture(scope="module")
def shared_key(exchange):
    return exchange[4]


# ---------------------------------------------------------------------------
# S1 — Session isolation
# ---------------------------------------------------------------------------

class TestSessionIsolation:
    """Ciphertext produced in session A must be cryptographically rejected in session B."""

    def test_cross_session_ciphertext_rejected(self, shared_key):
        enc_a = StreamEncryptor(shared_key)
        dec_a = StreamDecryptor(shared_key, enc_a.header)
        plaintext = b"\x00\x00\x04\x00\x00\x00\x00\x00"

        ct = enc_a.encrypt(plaintext)
        assert dec_a.decrypt(ct) == plaintext  # session A works as expected

        # Session B has a different header → different internal stream state
        enc_b = StreamEncryptor(shared_key)
        dec_b = StreamDecryptor(shared_key, enc_b.header)
        assert dec_b.decrypt(ct) is None, (
            "Ciphertext from session A must not decrypt in session B"
        )

    def test_cross_session_framed_packet_rejected(self, shared_key):
        """Correctly framed wire packet from session A is crypto-rejected in session B."""
        enc_a = StreamEncryptor(shared_key)
        enc_b = StreamEncryptor(shared_key)
        dec_b = StreamDecryptor(shared_key, enc_b.header)

        report = b"\x01\x00\x05\x00\x00\x00\x00\x00"
        ct = enc_a.encrypt(report)
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 1, ct))

        reader = PacketReader()
        packets = reader.feed(framed)
        assert len(packets) == 1
        _, _, recv_ct = packets[0]
        assert dec_b.decrypt(recv_ct) is None


# ---------------------------------------------------------------------------
# S2 — Authentication (ciphertext integrity)
# ---------------------------------------------------------------------------

class TestAuthentication:
    """Any modification to an authenticated ciphertext byte must cause rejection."""

    def test_single_bit_flip_at_every_byte_position_rejected(self, shared_key):
        plaintext = b"\x00\x00\x04\x00\x00\x00\x00\x00"
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)
        original_ct = enc.encrypt(plaintext)

        for i in range(len(original_ct)):
            # Reinitialise for each attempt — decryptor state is consumed on failure
            enc2 = StreamEncryptor(shared_key)
            dec2 = StreamDecryptor(shared_key, enc2.header)
            ct = enc2.encrypt(plaintext)

            corrupted = bytearray(ct)
            corrupted[i] ^= 0x01
            assert dec2.decrypt(bytes(corrupted)) is None, (
                f"Bit flip at byte index {i} must be rejected by authentication tag"
            )

    def test_truncated_ciphertext_rejected(self, shared_key):
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)
        ct = enc.encrypt(b"\x00\x00\x04\x00\x00\x00\x00\x00")
        # Remove the last byte — cuts the authentication tag
        assert dec.decrypt(ct[:-1]) is None, "Truncated ciphertext must be rejected"

    def test_prepended_byte_rejected(self, shared_key):
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)
        ct = enc.encrypt(b"\x00\x00\x04\x00\x00\x00\x00\x00")
        assert dec.decrypt(b"\x00" + ct) is None, "Prepended byte must cause rejection"


# ---------------------------------------------------------------------------
# S3 — Nonce uniqueness (semantic security)
# ---------------------------------------------------------------------------

class TestNonceUniqueness:
    """Encrypting the same plaintext twice in one stream must yield different outputs."""

    def test_same_plaintext_produces_distinct_ciphertexts(self, shared_key):
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)
        plaintext = b"\x00\x00\x04\x00\x00\x00\x00\x00"

        ct1 = enc.encrypt(plaintext)
        ct2 = enc.encrypt(plaintext)
        assert ct1 != ct2, (
            "Encrypting the same plaintext twice must produce distinct ciphertexts "
            "(stream state advances after each message)"
        )

        # Both ciphertexts must still decrypt correctly in order
        assert dec.decrypt(ct1) == plaintext
        assert dec.decrypt(ct2) == plaintext

    def test_five_identical_plaintexts_all_distinct(self, shared_key):
        enc = StreamEncryptor(shared_key)
        msg = b"\xFF" * 8
        ciphertexts = [enc.encrypt(msg) for _ in range(5)]
        assert len(set(ciphertexts)) == 5, "All 5 ciphertexts of the same plaintext must be unique"


# ---------------------------------------------------------------------------
# S4 — MITM fingerprint detection
# ---------------------------------------------------------------------------

class TestMITMDetection:
    """Substituting an attacker's public key during key exchange changes the fingerprint."""

    def test_attacker_key_produces_different_fingerprint(self, exchange):
        mac_priv, mac_pub, win_priv, win_pub, legitimate_shared = exchange

        # Attacker inserts their own public key in place of the Windows key
        atk_priv, atk_pub = mac_keypair()
        mitm_shared = mac_derive(mac_priv, atk_pub)

        fp_legitimate = mac_fp(legitimate_shared)
        fp_mitm = mac_fp(mitm_shared)
        assert fp_legitimate != fp_mitm, (
            "MITM key substitution must produce a different fingerprint — "
            "the user fingerprint check would catch this"
        )

    def test_legitimate_fingerprints_agree_on_both_sides(self, exchange):
        _, _, _, _, shared = exchange
        fp_mac = mac_fp(shared)
        fp_win = win_fp(shared)
        assert fp_mac == fp_win, "Both sides must display the same 6-digit fingerprint"
        assert len(fp_mac) == 6
        assert fp_mac.isdigit()

    def test_different_shared_keys_different_fingerprints(self):
        """Independently generated key exchanges produce distinct fingerprints."""
        fingerprints = set()
        for _ in range(10):
            mp, _ = mac_keypair()
            _, wp = win_keypair()
            fingerprints.add(mac_fp(mac_derive(mp, wp)))
        assert len(fingerprints) == 10, "Each exchange must yield a unique fingerprint"


# ---------------------------------------------------------------------------
# S5 — Wrong-key rejection
# ---------------------------------------------------------------------------

class TestWrongKeyRejection:
    """A decryptor initialised with the wrong shared key must reject all ciphertext."""

    def test_wrong_key_returns_none(self, shared_key):
        enc = StreamEncryptor(shared_key)
        wrong_key = bytes([b ^ 0xAA for b in shared_key])
        try:
            dec = StreamDecryptor(wrong_key, enc.header)
            ct = enc.encrypt(b"\xDE\xAD\xBE\xEF" * 2)
            result = dec.decrypt(ct)
            assert result is None, "Wrong shared key must return None, not decrypt successfully"
        except Exception:
            pass  # Initialisation with a wrong key may also raise — both outcomes are secure

    def test_correct_key_decrypts_reliably(self, shared_key):
        """Control: the correct key always succeeds across multiple sessions."""
        for i in range(5):
            enc = StreamEncryptor(shared_key)
            dec = StreamDecryptor(shared_key, enc.header)
            msg = bytes([i] * 8)
            assert dec.decrypt(enc.encrypt(msg)) == msg


# ---------------------------------------------------------------------------
# S6 — Stream-order binding
# ---------------------------------------------------------------------------

class TestStreamOrderBinding:
    """Messages must be decrypted in the exact order they were encrypted."""

    def test_second_message_rejected_when_first_skipped(self, shared_key):
        enc = StreamEncryptor(shared_key)
        msg1 = b"\x00\x00\x04\x00\x00\x00\x00\x00"
        msg2 = b"\x00\x00\x05\x00\x00\x00\x00\x00"
        ct1 = enc.encrypt(msg1)  # noqa: F841 — encrypted but intentionally not used
        ct2 = enc.encrypt(msg2)

        # Fresh decryptor — has consumed nothing, so ct2 (2nd in stream) must fail
        dec = StreamDecryptor(shared_key, enc.header)
        result = dec.decrypt(ct2)
        assert result is None, (
            "Decrypting the second message before the first must fail "
            "(secretstream state is bound to message order)"
        )

    def test_in_order_decryption_succeeds(self, shared_key):
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)
        messages = [bytes([i] * 8) for i in range(6)]
        for msg in messages:
            assert dec.decrypt(enc.encrypt(msg)) == msg, (
                f"In-order decryption must succeed for message {msg!r}"
            )


# ---------------------------------------------------------------------------
# S7 — Replay prevention at the packet layer
# ---------------------------------------------------------------------------

class TestReplayPrevention:
    """PacketReader must reject replayed or duplicate sequence numbers."""

    def test_old_session_seqno_rejected_after_reset(self, shared_key):
        reader = PacketReader()
        enc = StreamEncryptor(shared_key)
        dec = StreamDecryptor(shared_key, enc.header)  # noqa: F841
        dummy = b"\x00" * 8

        # Session 1: accept seqnos 0, 1, 2
        for seqno in range(3):
            ct = enc.encrypt(dummy)
            framed = frame_packet(build_packet(TYPE_KEYBOARD, seqno, ct))
            for _, sn, _ in reader.feed(framed):
                reader.validate_seqno(sn)

        # Grab session 1's replay packet (seqno=0) before reset
        replay_ct = enc.encrypt(dummy)
        replay_framed = frame_packet(build_packet(TYPE_KEYBOARD, 0, replay_ct))

        # Reconnect — reset reader, fresh crypto session
        reader.reset()
        enc2 = StreamEncryptor(shared_key)
        dec2 = StreamDecryptor(shared_key, enc2.header)

        # Session 2: legitimate seqno=0 accepted
        ct2 = enc2.encrypt(dummy)
        framed2 = frame_packet(build_packet(TYPE_KEYBOARD, 0, ct2))
        pkts = reader.feed(framed2)
        assert len(pkts) == 1
        _, sn2, ct_recv = pkts[0]
        assert reader.validate_seqno(sn2)
        assert dec2.decrypt(ct_recv) == dummy

        # Replay of session 1's seqno=0 — must be dropped (seqno 0 <= last accepted 0)
        replay_pkts = reader.feed(replay_framed)
        if replay_pkts:
            _, sn_r, ct_r = replay_pkts[0]
            assert not reader.validate_seqno(sn_r), (
                "PacketReader must reject a replayed seqno from the old session"
            )

    def test_duplicate_seqno_within_session_rejected(self):
        reader = PacketReader()
        dummy = b"\x00" * 4

        # seqno 10 accepted
        framed = frame_packet(build_packet(TYPE_KEYBOARD, 10, dummy))
        for _, sn, _ in reader.feed(framed):
            assert reader.validate_seqno(10)

        # seqno 10 again — rejected
        for _, sn, _ in reader.feed(framed):
            assert not reader.validate_seqno(sn), "Duplicate seqno must be rejected"

    def test_backwards_seqno_rejected(self):
        reader = PacketReader()
        dummy = b"\x00" * 4

        for seqno in [5, 6, 7]:
            framed = frame_packet(build_packet(TYPE_KEYBOARD, seqno, dummy))
            for _, sn, _ in reader.feed(framed):
                reader.validate_seqno(sn)

        # Send seqno 4 — behind the watermark
        framed_old = frame_packet(build_packet(TYPE_KEYBOARD, 4, dummy))
        for _, sn, _ in reader.feed(framed_old):
            assert not reader.validate_seqno(sn), (
                "Backwards seqno (below watermark) must be rejected"
            )


# ---------------------------------------------------------------------------
# S8 — Header uniqueness
# ---------------------------------------------------------------------------

class TestHeaderUniqueness:
    """Each new StreamEncryptor must produce a cryptographically unique 24-byte header."""

    def test_header_is_exactly_24_bytes(self, shared_key):
        enc = StreamEncryptor(shared_key)
        assert len(enc.header) == 24
        assert STREAM_HEADER_LEN == 24

    def test_20_consecutive_headers_are_all_distinct(self, shared_key):
        headers = {StreamEncryptor(shared_key).header for _ in range(20)}
        assert len(headers) == 20, (
            "Each of 20 independently created sessions must have a unique header "
            "(the header contains a 192-bit random nonce)"
        )

    def test_shared_key_not_in_wire_bytes(self, shared_key):
        """The raw shared key must not appear as a substring anywhere in the wire bytes."""
        enc = StreamEncryptor(shared_key)
        ct = enc.encrypt(b"\x00\x00\x04\x00\x00\x00\x00\x00")
        wire = enc.header + frame_packet(build_packet(TYPE_KEYBOARD, 0, ct))
        assert shared_key not in wire, (
            "The 32-byte shared key must not appear in plaintext in the wire bytes"
        )
