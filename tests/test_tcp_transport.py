"""
Task 9 — TCP Transport integration tests.

Tests the TCPServer and TCPClient implementations against each other on
localhost. No network hardware required — all tests use 127.0.0.1.

Test scenarios:
  1. Connect / disconnect round-trip
  2. Data delivery (100 framed packets)
  3. Auto-reconnect after client restart
  4. Full pipeline: encrypted keyboard packets server→client
  5. Concurrent sends (thread safety)
  6. Clean shutdown (stop() joins threads within 5s)
"""

import socket
import struct
import threading
import time
import pytest

from keybridgebt_mac.tcp_server import TCPServer
from keybridgebt_win.tcp_client import TCPClient

# Shared crypto helpers for the pipeline test
from keybridgebt_mac.packet import build_packet, frame_packet, next_seqno, TYPE_KEYBOARD
from keybridgebt_mac.crypto import generate_keypair as mac_keygen, derive_shared_key, StreamEncryptor
from keybridgebt_win.packet import PacketReader
from keybridgebt_win.crypto import StreamDecryptor, STREAM_HEADER_LEN


# ---------------------------------------------------------------------------
# Helper: pick a free port so parallel test runs don't collide
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Scenario 1 — Connect / disconnect round-trip
# ---------------------------------------------------------------------------

class TestConnectDisconnect:
    def test_on_connect_fires_on_both_sides(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)

        server_connected = threading.Event()
        server_disconnected = threading.Event()
        client_connected = threading.Event()
        client_disconnected = threading.Event()

        server.set_callbacks(
            on_connect=lambda: server_connected.set(),
            on_disconnect=lambda: server_disconnected.set(),
        )

        server.start()

        client = TCPClient(host="127.0.0.1", port=port)
        client.set_callbacks(
            on_connect=lambda: client_connected.set(),
            on_disconnect=lambda: client_disconnected.set(),
        )
        client.start()

        try:
            assert server_connected.wait(timeout=5), "server on_connect did not fire"
            assert client_connected.wait(timeout=5), "client on_connect did not fire"
            assert server.is_connected
            assert client.is_connected
        finally:
            client.stop()
            server.stop()

    def test_on_disconnect_fires_when_client_stops(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server_disconnected = threading.Event()
        server.set_callbacks(on_disconnect=lambda: server_disconnected.set())
        server.start()

        client = TCPClient(host="127.0.0.1", port=port)
        client.start()

        try:
            assert server.wait_for_connection(timeout=5), "client never connected"
            client.stop()
            assert server_disconnected.wait(timeout=5), "server on_disconnect did not fire"
            assert not server.is_connected
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Scenario 2 — Data delivery: server sends 100 framed packets
# ---------------------------------------------------------------------------

class TestDataDelivery:
    def test_100_packets_delivered_intact(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.start()

        received_chunks = []
        received_lock = threading.Lock()

        def on_data(data: bytes):
            with received_lock:
                received_chunks.append(data)

        client = TCPClient(host="127.0.0.1", port=port, callback=on_data)
        client.start()

        try:
            assert server.wait_for_connection(timeout=5)
            time.sleep(0.1)  # let on_connect fire

            payloads = [
                frame_packet(build_packet(TYPE_KEYBOARD, i, bytes([i % 256]) * 8))
                for i in range(100)
            ]
            for p in payloads:
                assert server.send(p)

            # Give all data time to arrive
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with received_lock:
                    total = sum(len(c) for c in received_chunks)
                expected_total = sum(len(p) for p in payloads)
                if total >= expected_total:
                    break
                time.sleep(0.05)

            with received_lock:
                all_data = b"".join(received_chunks)

            assert all_data == b"".join(payloads), "received data does not match sent data"
        finally:
            client.stop()
            server.stop()


# ---------------------------------------------------------------------------
# Scenario 3 — Auto-reconnect
# ---------------------------------------------------------------------------

class TestAutoReconnect:
    def test_client_reconnects_after_server_restart(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        connect_events = []
        connect_lock = threading.Lock()

        def on_server_connect():
            with connect_lock:
                connect_events.append(time.monotonic())

        server.set_callbacks(on_connect=on_server_connect)
        server.start()

        client = TCPClient(host="127.0.0.1", port=port)
        client.start()

        try:
            # Wait for first connection
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with connect_lock:
                    if connect_events:
                        break
                time.sleep(0.1)
            assert len(connect_events) >= 1, "initial connection never established"

            # Force-close the client socket from the server side by stopping+restarting server
            second_connect = threading.Event()
            server.set_callbacks(on_connect=lambda: second_connect.set())
            # Close the active client abruptly by stopping the server briefly
            server.stop()
            time.sleep(0.2)
            server = TCPServer(host="127.0.0.1", port=port)
            server.set_callbacks(on_connect=lambda: second_connect.set())
            server.start()

            assert second_connect.wait(timeout=10), "client did not reconnect"
        finally:
            client.stop()
            server.stop()


# ---------------------------------------------------------------------------
# Scenario 4 — Full pipeline: stream header + encrypted keyboard packets
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_encrypt_send_decrypt_receive(self):
        port = _free_port()

        # Generate a shared secret
        mac_priv, mac_pub = mac_keygen()
        shared_key = derive_shared_key(mac_priv, mac_pub)  # self-loop for testing

        server = TCPServer(host="127.0.0.1", port=port)
        server.start()

        received_plaintexts = []
        received_lock = threading.Lock()
        reader = PacketReader()
        decryptor_holder = [None]
        awaiting_header = [True]
        header_buf = bytearray()

        def on_data(data: bytes):
            nonlocal header_buf
            if awaiting_header[0]:
                header_buf.extend(data)
                if len(header_buf) >= STREAM_HEADER_LEN:
                    header = bytes(header_buf[:STREAM_HEADER_LEN])
                    leftover = bytes(header_buf[STREAM_HEADER_LEN:])
                    decryptor_holder[0] = StreamDecryptor(shared_key, header)
                    awaiting_header[0] = False
                    if leftover:
                        _process(leftover)
                return
            _process(data)

        def _process(data: bytes):
            dec = decryptor_holder[0]
            if dec is None:
                return
            packets = reader.feed(data)
            for ptype, seqno, ciphertext in packets:
                plaintext = dec.decrypt(ciphertext)
                if plaintext is not None:
                    with received_lock:
                        received_plaintexts.append(plaintext)

        client = TCPClient(host="127.0.0.1", port=port, callback=on_data)
        client.start()

        try:
            assert server.wait_for_connection(timeout=5)
            time.sleep(0.1)

            # Encrypt and send 5 keyboard reports
            encryptor = StreamEncryptor(shared_key)
            server.send(encryptor.header)  # raw header bootstrap

            original_reports = []
            seqno = 0
            for i in range(5):
                report = bytes([0x00, 0x00, 0x04 + i, 0x00, 0x00, 0x00, 0x00, 0x00])
                ciphertext = encryptor.encrypt(report)
                packet = build_packet(TYPE_KEYBOARD, seqno, ciphertext)
                server.send(frame_packet(packet))
                original_reports.append(report)
                seqno = next_seqno(seqno)
                time.sleep(0.01)

            # Wait for all 5 to arrive
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with received_lock:
                    if len(received_plaintexts) >= 5:
                        break
                time.sleep(0.05)

            with received_lock:
                got = list(received_plaintexts)

            assert len(got) == 5, f"expected 5 decrypted reports, got {len(got)}"
            for orig, dec_plain in zip(original_reports, got):
                assert orig == dec_plain, f"decrypted report mismatch: {orig!r} != {dec_plain!r}"
        finally:
            client.stop()
            server.stop()


# ---------------------------------------------------------------------------
# Scenario 5 — Concurrent sends (thread safety)
# ---------------------------------------------------------------------------

class TestConcurrentSends:
    def test_no_data_corruption_under_concurrent_sends(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.start()

        received_chunks = []
        lock = threading.Lock()

        def on_data(data: bytes):
            with lock:
                received_chunks.append(data)

        client = TCPClient(host="127.0.0.1", port=port, callback=on_data)
        client.start()

        try:
            assert server.wait_for_connection(timeout=5)
            time.sleep(0.1)

            N_THREADS = 5
            N_SENDS = 20
            # Each thread sends a fixed-content payload; total 100 sends.
            payload = b"ABCD" * 16  # 64 bytes per send
            barrier = threading.Barrier(N_THREADS)
            errors = []

            def sender(tid: int):
                barrier.wait()
                for _ in range(N_SENDS):
                    ok = server.send(payload)
                    if not ok:
                        errors.append(tid)

            threads = [threading.Thread(target=sender, args=(i,)) for i in range(N_THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"send failed in threads {errors}"

            expected_total = N_THREADS * N_SENDS * len(payload)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with lock:
                    got_total = sum(len(c) for c in received_chunks)
                if got_total >= expected_total:
                    break
                time.sleep(0.05)

            with lock:
                got_total = sum(len(c) for c in received_chunks)

            assert got_total == expected_total, (
                f"expected {expected_total} bytes, got {got_total}"
            )
        finally:
            client.stop()
            server.stop()


# ---------------------------------------------------------------------------
# Scenario 6 — Clean shutdown: stop() joins threads within 5 seconds
# ---------------------------------------------------------------------------

class TestCleanShutdown:
    def test_server_stop_joins_thread(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.start()
        assert server._thread is not None
        server.stop()
        assert not server.is_connected
        # Thread should be gone after stop()
        assert server._thread is None

    def test_client_stop_joins_thread(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.start()

        client = TCPClient(host="127.0.0.1", port=port)
        client.start()
        assert server.wait_for_connection(timeout=5)

        client.stop()
        assert not client.is_connected
        assert client._thread is None

        server.stop()

    def test_server_stop_before_any_connection(self):
        port = _free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.start()
        time.sleep(0.1)
        server.stop()  # should not raise or hang
        assert not server.is_connected

    def test_client_stop_while_reconnecting(self):
        """Client should stop cleanly even while between connection attempts."""
        port = _free_port()  # nothing listening on this port
        client = TCPClient(host="127.0.0.1", port=port)
        client.start()
        time.sleep(0.3)  # let it attempt at least one connect
        client.stop()    # must not hang
        assert not client.is_connected
