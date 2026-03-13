"""
Wire packet builder for mac-sender.

Packet format: [type(1)] [seqno(4 LE)] [encrypted_payload]
Framing:       [length(2 LE)] [packet]

See docs/ARCHITECTURE.md §2 for full wire format spec.
"""

import struct

MAX_PACKET_SIZE = 65535
HEADER_FMT = "<BL"          # type (uint8) + seqno (uint32 LE)
HEADER_SIZE = struct.calcsize(HEADER_FMT)
LENGTH_FMT = "<H"           # frame length prefix (uint16 LE)
LENGTH_SIZE = struct.calcsize(LENGTH_FMT)

TYPE_KEYBOARD = 0x01
TYPE_POINTER = 0x02


def build_packet(ptype: int, seqno: int, encrypted_payload: bytes) -> bytes:
    """Build a wire packet: [type][seqno][encrypted_payload]."""
    header = struct.pack(HEADER_FMT, ptype, seqno)
    return header + encrypted_payload


def frame_packet(packet: bytes) -> bytes:
    """Length-prefix a packet: [len_u16_le][packet]."""
    if len(packet) > MAX_PACKET_SIZE:
        raise ValueError(f"Packet too large: {len(packet)} > {MAX_PACKET_SIZE}")
    return struct.pack(LENGTH_FMT, len(packet)) + packet


def next_seqno(current: int) -> int:
    """Increment and return next sequence number (wraps at 2^32)."""
    return (current + 1) & 0xFFFFFFFF
