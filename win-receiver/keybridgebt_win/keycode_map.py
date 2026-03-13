"""
Complete HID Usage ID (Keyboard/Keypad page 0x07) → Windows Virtual-Key code map.

Includes validation whitelist of all legitimate keyboard HID codes.

Reference:
  - USB HID Usage Tables §10
  - Microsoft Virtual-Key Codes

See docs/TASKS.md Task 11.
"""

# fmt: off
HID_TO_VK: dict[int, int] = {
    # Letters  (HID 0x04–0x1D → VK 0x41–0x5A)
    0x04: 0x41,  # A
    0x05: 0x42,  # B
    0x06: 0x43,  # C
    0x07: 0x44,  # D
    0x08: 0x45,  # E
    0x09: 0x46,  # F
    0x0A: 0x47,  # G
    0x0B: 0x48,  # H
    0x0C: 0x49,  # I
    0x0D: 0x4A,  # J
    0x0E: 0x4B,  # K
    0x0F: 0x4C,  # L
    0x10: 0x4D,  # M
    0x11: 0x4E,  # N
    0x12: 0x4F,  # O
    0x13: 0x50,  # P
    0x14: 0x51,  # Q
    0x15: 0x52,  # R
    0x16: 0x53,  # S
    0x17: 0x54,  # T
    0x18: 0x55,  # U
    0x19: 0x56,  # V
    0x1A: 0x57,  # W
    0x1B: 0x58,  # X
    0x1C: 0x59,  # Y
    0x1D: 0x5A,  # Z

    # Digits  (HID 0x1E–0x27 → VK 0x31–0x30)
    0x1E: 0x31,  # 1
    0x1F: 0x32,  # 2
    0x20: 0x33,  # 3
    0x21: 0x34,  # 4
    0x22: 0x35,  # 5
    0x23: 0x36,  # 6
    0x24: 0x37,  # 7
    0x25: 0x38,  # 8
    0x26: 0x39,  # 9
    0x27: 0x30,  # 0

    # Control keys
    0x28: 0x0D,  # Enter             → VK_RETURN
    0x29: 0x1B,  # Escape            → VK_ESCAPE
    0x2A: 0x08,  # Backspace         → VK_BACK
    0x2B: 0x09,  # Tab               → VK_TAB
    0x2C: 0x20,  # Space             → VK_SPACE

    # Symbols row
    0x2D: 0xBD,  # - _               → VK_OEM_MINUS
    0x2E: 0xBB,  # = +               → VK_OEM_PLUS
    0x2F: 0xDB,  # [ {               → VK_OEM_4
    0x30: 0xDD,  # ] }               → VK_OEM_6
    0x31: 0xDC,  # \ |               → VK_OEM_5
    0x32: 0xDC,  # Non-US # ~        → VK_OEM_5 (fallback)
    0x33: 0xBA,  # ; :               → VK_OEM_1
    0x34: 0xDE,  # ' "               → VK_OEM_7
    0x35: 0xC0,  # ` ~               → VK_OEM_3
    0x36: 0xBC,  # , <               → VK_OEM_COMMA
    0x37: 0xBE,  # . >               → VK_OEM_PERIOD
    0x38: 0xBF,  # / ?               → VK_OEM_2

    # Caps Lock
    0x39: 0x14,  # Caps Lock         → VK_CAPITAL

    # Function keys  (HID 0x3A–0x45 → VK 0x70–0x7B)
    0x3A: 0x70,  # F1
    0x3B: 0x71,  # F2
    0x3C: 0x72,  # F3
    0x3D: 0x73,  # F4
    0x3E: 0x74,  # F5
    0x3F: 0x75,  # F6
    0x40: 0x76,  # F7
    0x41: 0x77,  # F8
    0x42: 0x78,  # F9
    0x43: 0x79,  # F10
    0x44: 0x7A,  # F11
    0x45: 0x7B,  # F12

    # Print / Scroll / Pause cluster
    0x46: 0x2C,  # Print Screen      → VK_SNAPSHOT
    0x47: 0x91,  # Scroll Lock       → VK_SCROLL
    0x48: 0x13,  # Pause             → VK_PAUSE

    # Navigation cluster
    0x49: 0x2D,  # Insert            → VK_INSERT
    0x4A: 0x24,  # Home              → VK_HOME
    0x4B: 0x21,  # Page Up           → VK_PRIOR
    0x4C: 0x2E,  # Delete Forward    → VK_DELETE
    0x4D: 0x23,  # End               → VK_END
    0x4E: 0x22,  # Page Down         → VK_NEXT

    # Arrow keys
    0x4F: 0x27,  # Right Arrow       → VK_RIGHT
    0x50: 0x25,  # Left Arrow        → VK_LEFT
    0x51: 0x28,  # Down Arrow        → VK_DOWN
    0x52: 0x26,  # Up Arrow          → VK_UP

    # Numpad
    0x53: 0x90,  # Num Lock          → VK_NUMLOCK
    0x54: 0x6F,  # KP /              → VK_DIVIDE
    0x55: 0x6A,  # KP *              → VK_MULTIPLY
    0x56: 0x6D,  # KP -              → VK_SUBTRACT
    0x57: 0x6B,  # KP +              → VK_ADD
    0x58: 0x0D,  # KP Enter          → VK_RETURN
    0x59: 0x61,  # KP 1              → VK_NUMPAD1
    0x5A: 0x62,  # KP 2              → VK_NUMPAD2
    0x5B: 0x63,  # KP 3              → VK_NUMPAD3
    0x5C: 0x64,  # KP 4              → VK_NUMPAD4
    0x5D: 0x65,  # KP 5              → VK_NUMPAD5
    0x5E: 0x66,  # KP 6              → VK_NUMPAD6
    0x5F: 0x67,  # KP 7              → VK_NUMPAD7
    0x60: 0x68,  # KP 8              → VK_NUMPAD8
    0x61: 0x69,  # KP 9              → VK_NUMPAD9
    0x62: 0x60,  # KP 0              → VK_NUMPAD0
    0x63: 0x6E,  # KP .              → VK_DECIMAL

    # Extra keys
    0x64: 0xE2,  # Non-US \ |        → VK_OEM_102
    0x65: 0x5D,  # Application       → VK_APPS
    0x67: 0xBB,  # KP =              → VK_OEM_PLUS (approx)

    # Extended function keys  (F13–F24)
    0x68: 0x7C,  # F13
    0x69: 0x7D,  # F14
    0x6A: 0x7E,  # F15
    0x6B: 0x7F,  # F16
    0x6C: 0x80,  # F17
    0x6D: 0x81,  # F18
    0x6E: 0x82,  # F19
    0x6F: 0x83,  # F20
    0x70: 0x84,  # F21
    0x71: 0x85,  # F22
    0x72: 0x86,  # F23
    0x73: 0x87,  # F24

    # Media / system keys (common HID usages)
    0x7F: 0xAD,  # Mute              → VK_VOLUME_MUTE
    0x80: 0xAF,  # Volume Up         → VK_VOLUME_UP
    0x81: 0xAE,  # Volume Down       → VK_VOLUME_DOWN
}

# HID modifier bit positions → Windows VK codes
HID_MOD_TO_VK: dict[int, int] = {
    0x01: 0xA2,  # Left Ctrl         → VK_LCONTROL
    0x02: 0xA0,  # Left Shift        → VK_LSHIFT
    0x04: 0xA4,  # Left Alt          → VK_LMENU
    0x08: 0x5B,  # Left GUI (⌘/Win) → VK_LWIN
    0x10: 0xA3,  # Right Ctrl        → VK_RCONTROL
    0x20: 0xA1,  # Right Shift       → VK_RSHIFT
    0x40: 0xA5,  # Right Alt         → VK_RMENU
    0x80: 0x5C,  # Right GUI         → VK_RWIN
}

# Extended key set — needs KEYEVENTF_EXTENDEDKEY in SendInput
EXTENDED_VK: set[int] = {
    0x21, 0x22, 0x23, 0x24,  # PgUp, PgDn, End, Home
    0x25, 0x26, 0x27, 0x28,  # Arrow keys
    0x2C, 0x2D, 0x2E,        # PrtSc, Insert, Delete
    0x5B, 0x5C, 0x5D,        # LWin, RWin, Apps
    0x6F,                     # Numpad /
    0x90,                     # NumLock
    0xA3, 0xA5,               # RCtrl, RAlt
}

# Validation whitelist — all legitimate HID keyboard usage IDs
# Only keycodes in this set are allowed through to injection
VALID_HID_RANGE: set[int] = set(HID_TO_VK.keys())
# fmt: on
