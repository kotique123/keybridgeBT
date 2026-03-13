"""
Wire packet parser for win-receiver.

Deframes length-prefixed packets from the RFCOMM byte stream,
validates sequence numbers, and returns (type, seqno, encrypted_payload).

See docs/ARCHITECTURE.md §2 and docs/TASKS.md Task 2.
"""

import struct
import logging

log = logging.getLogger(__name__)

HEADER_FMT = "<BL"          # type (uint8) + seqno (uint32 LE)
HEADER_SIZE = struct.calcsize(HEADER_FMT)
LENGTH_FMT = "<H"           # frame length prefix (uint16 LE)
LENGTH_SIZE = struct.calcsize(LENGTH_FMT)

MAX_PACKET_SIZE = 65535

TYPE_KEYBOARD = 0x01
TYPE_POINTER = 0x02


class PacketReader:
    """Stateful reader that deframes length-prefixed packets from a byte stream."""

    def __init__(self):
        self._buffer = bytearray()
        self._last_seqno = -1

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        """
        Feed raw bytes from the serial stream.
        Returns a list of complete parsed packets: [(ptype, seqno, encrypted_payload), ...]
        """
        self._buffer.extend(data)
        results = []

        while len(self._buffer) >= LENGTH_SIZE:
            # Peek at the length prefix
            pkt_len = struct.unpack_from(LENGTH_FMT, self._buffer, 0)[0]

            if pkt_len > MAX_PACKET_SIZE:
                log.warning("Packet too large (%d), dropping buffer", pkt_len)
                self._buffer.clear()
                break

            total_needed = LENGTH_SIZE + pkt_len
            if len(self._buffer) < total_needed:
                break  # incomplete packet, wait for more data

            # Extract the packet (skip the length prefix)
            packet = bytes(self._buffer[LENGTH_SIZE:total_needed])
            del self._buffer[:total_needed]

            if len(packet) < HEADER_SIZE:
                log.warning("Packet too short (%d bytes), skipping", len(packet))
                continue

            ptype, seqno = struct.unpack_from(HEADER_FMT, packet, 0)
            encrypted_payload = packet[HEADER_SIZE:]
            results.append((ptype, seqno, encrypted_payload))

        return results

    def validate_seqno(self, seqno: int) -> bool:
        """Return True if seqno is strictly greater than last seen."""
        if seqno <= self._last_seqno:
            log.debug("Out-of-order/duplicate seqno: %d (last: %d)", seqno, self._last_seqno)
            return False
        self._last_seqno = seqno
        return True

    def reset(self):
        """Reset state for a new session."""
        self._buffer.clear()
        self._last_seqno = -1
