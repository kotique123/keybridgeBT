"""
Keyboard injection via SendInput on Windows.

Translates HID boot-protocol keyboard reports into Windows virtual-key
events and injects them at the system level. Validates all keycodes
against a whitelist before injection.

See docs/ARCHITECTURE.md §5.7 and docs/TASKS.md Task 12.
"""

import logging
import ctypes
from ctypes import wintypes

from .keycode_map import HID_TO_VK, HID_MOD_TO_VK, EXTENDED_VK, VALID_HID_RANGE

log = logging.getLogger(__name__)

# Win32 constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR — pointer-sized on 32/64-bit
    ]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = ctypes.c_uint


class KeyInjector:
    """Inject keyboard events from HID reports via Windows SendInput."""

    def __init__(self):
        self._prev_modifiers = 0
        self._prev_keys = set()

    def inject_report(self, report: bytes):
        """
        Process an 8-byte HID keyboard report.
        Validates keycodes against whitelist, diffs for press/release, injects.
        """
        if len(report) < 8:
            return

        modifier = report[0]
        # Filter keycodes through whitelist
        keys = set()
        for k in report[2:8]:
            if k == 0:
                continue
            if k not in VALID_HID_RANGE:
                log.warning("Rejected invalid HID keycode: 0x%02X", k)
                continue
            keys.add(k)

        inputs = []

        # Modifier changes
        for bit, vk in HID_MOD_TO_VK.items():
            was_pressed = bool(self._prev_modifiers & bit)
            is_pressed = bool(modifier & bit)
            if is_pressed and not was_pressed:
                inputs.append(self._make_key_input(vk, down=True))
            elif not is_pressed and was_pressed:
                inputs.append(self._make_key_input(vk, down=False))

        # Key releases
        for hid_code in self._prev_keys - keys:
            vk = HID_TO_VK.get(hid_code)
            if vk:
                inputs.append(self._make_key_input(vk, down=False))

        # Key presses
        for hid_code in keys - self._prev_keys:
            vk = HID_TO_VK.get(hid_code)
            if vk:
                inputs.append(self._make_key_input(vk, down=True))

        if inputs:
            arr = (INPUT * len(inputs))(*inputs)
            sent = SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
            if sent != len(inputs):
                log.warning("SendInput: sent %d/%d events", sent, len(inputs))

        self._prev_modifiers = modifier
        self._prev_keys = keys

    def release_all(self):
        """Release all currently held keys and modifiers. Call on disconnect."""
        inputs = []
        for bit, vk in HID_MOD_TO_VK.items():
            if self._prev_modifiers & bit:
                inputs.append(self._make_key_input(vk, down=False))
        for hid_code in self._prev_keys:
            vk = HID_TO_VK.get(hid_code)
            if vk:
                inputs.append(self._make_key_input(vk, down=False))
        if inputs:
            arr = (INPUT * len(inputs))(*inputs)
            SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        self._prev_modifiers = 0
        self._prev_keys = set()

    @staticmethod
    def _make_key_input(vk: int, down: bool) -> INPUT:
        flags = 0
        if not down:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_VK:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.wScan = 0
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = 0
        return inp
