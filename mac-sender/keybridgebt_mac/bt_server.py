"""
Bluetooth RFCOMM server using PyObjC IOBluetooth bindings.

Publishes an SDP service, enforces link-level encryption, streams
length-prefixed encrypted packets to the connected client.
Handles disconnect and waits for reconnection automatically.
Sets Mac to non-discoverable after first successful pairing.

See docs/ARCHITECTURE.md §4.3 for full spec.
"""

import threading
import logging
import time
import objc
from Foundation import NSObject
from CoreFoundation import (
    CFRunLoopGetCurrent,
    CFRunLoopRunInMode,
    kCFRunLoopDefaultMode,
)

log = logging.getLogger(__name__)

# RFCOMM channel ID advertised in the SDP record (1–30).
# Both sides must agree on this value at pairing time.
RFCOMM_CHANNEL_ID = 1

try:
    import IOBluetooth
except ImportError:
    IOBluetooth = None
    log.warning("IOBluetooth not available — RFCOMM will not work")


class RFCOMMDelegate(NSObject):
    """Objective-C delegate for IOBluetoothRFCOMMChannel events."""

    def initWithServer_(self, server):
        self = objc.super(RFCOMMDelegate, self).init()
        if self is None:
            return None
        self.server = server
        self._pending_channel = None
        return self

    # ------------------------------------------------------------------ #
    # Incoming-connection notification callback                           #
    # Called by IOBluetooth when a remote device opens RFCOMM_CHANNEL_ID #
    # DO NOT call openChannel() here — the channel is already being      #
    # opened by the remote side.  Just register this delegate and wait   #
    # for rfcommChannelOpenComplete_status_ to confirm it is open.       #
    # ------------------------------------------------------------------ #
    def newRFCOMMChannelOpened_channel_(self, notification, channel):
        log.info("Incoming RFCOMM channel notification (ID %s)", channel.getChannelID())
        self._pending_channel = channel
        channel.setDelegate_(self)
        # No openChannel() call — this is an *incoming* connection.
        # rfcommChannelOpenComplete_status_ fires when it is fully established.

    def rfcommChannelOpenComplete_status_(self, channel, status):
        if status == 0:
            log.info("RFCOMM channel open complete (ID %s)", channel.getChannelID())
            # Use pending_channel if channel arg is None (PyObjC bridge edge case)
            ch = channel if channel is not None else self._pending_channel
            self._pending_channel = None
            if ch is not None:
                self.server._on_client_connected(ch)
        else:
            log.error("RFCOMM channel open failed with status %d", status)
            self._pending_channel = None

    def rfcommChannelClosed_(self, channel):
        log.info("RFCOMM channel closed")
        self._pending_channel = None
        self.server._on_client_disconnected()

    def rfcommChannelData_data_length_(self, channel, data, length):
        log.debug("Received %d bytes from client (ignored)", length)


class RFCOMMServer:
    """
    Publish an RFCOMM service and stream data to connected clients.
    Thread-safe send(). Auto-waits for reconnection on disconnect.
    """

    def __init__(self, service_name: str = "keybridgeBT"):
        self._service_name = service_name
        self._channel = None
        self._delegate = None
        self._running = False
        self._thread = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._sdp_handle = None
        self._channel_notification = None
        self._first_pairing_done = False
        self._on_connect_callback = None
        self._on_disconnect_callback = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def set_callbacks(self, on_connect=None, on_disconnect=None):
        """Set callbacks for connection state changes."""
        self._on_connect_callback = on_connect
        self._on_disconnect_callback = on_disconnect

    def start(self):
        if IOBluetooth is None:
            raise RuntimeError("IOBluetooth framework not available")
        self._running = True
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._close_channel()
        if self._thread:
            self._thread.join(timeout=5)

    def send(self, data: bytes) -> bool:
        """Send raw bytes to the connected client. Thread-safe."""
        with self._lock:
            ch = self._channel
        if ch is None:
            return False
        try:
            result = ch.writeSync_length_(data, len(data))
            return result == 0
        except Exception:
            log.exception("RFCOMM send failed")
            return False

    def wait_for_connection(self, timeout=None) -> bool:
        return self._connected.wait(timeout=timeout)

    def set_non_discoverable(self):
        """Set Mac to non-discoverable after first pairing."""
        try:
            # IOBluetoothPreferenceSetDiscoverableState(0) via IOBluetooth
            if IOBluetooth and hasattr(IOBluetooth, "IOBluetoothPreferenceSetDiscoverableState"):
                IOBluetooth.IOBluetoothPreferenceSetDiscoverableState(0)
                log.info("Set Bluetooth to non-discoverable")
        except Exception:
            log.exception("Failed to set non-discoverable")

    def _enforce_link_encryption(self, channel) -> bool:
        """Check that BT link-level encryption is active on the channel's device."""
        try:
            device = channel.getDevice()
            if device is None:
                return False
            # Request encryption if not already active
            if hasattr(device, "requestAuthentication"):
                device.requestAuthentication()
            return True
        except Exception:
            log.exception("Failed to enforce link encryption")
            return False

    def _serve_loop(self):
        while self._running:
            try:
                self._publish_service()
                log.info(
                    "RFCOMM service published on channel %d, waiting for connections…\n"
                    "  On Windows: open COM5 (outgoing BT port) to connect.",
                    RFCOMM_CHANNEL_ID,
                )
                while self._running:
                    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
            except Exception:
                log.exception("RFCOMM server error")
                if self._running:
                    time.sleep(2)

    def _publish_service(self):
        self._delegate = RFCOMMDelegate.alloc().initWithServer_(self)
        uuid = IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x0003)
        service_dict = {
            "0001 - ServiceClassIDList": [uuid],
            "0004 - ProtocolDescriptorList": [
                [IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x0100)],
                [IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x0003),
                 {"DataElementType": 1, "DataElementSize": 1,
                  "DataElementValue": RFCOMM_CHANNEL_ID}],
            ],
            "0100 - ServiceName": self._service_name,
        }
        self._sdp_handle = IOBluetooth.IOBluetoothSDPServiceRecord.publishedServiceRecordWithDictionary_(
            service_dict
        )
        if self._sdp_handle is None:
            log.error("Failed to publish SDP service record")

        # Register to be notified when a client opens our RFCOMM channel.
        # The delegate's newRFCOMMChannelOpened_channel_() method will be called.
        self._channel_notification = (
            IOBluetooth.IOBluetoothRFCOMMChannel
            .registerForChannelOpenNotifications_selector_withChannelID_direction_(
                self._delegate,
                "newRFCOMMChannelOpened_channel_",
                RFCOMM_CHANNEL_ID,
                IOBluetooth.kIOBluetoothUserNotificationChannelDirectionIncoming,
            )
        )
        if self._channel_notification is None:
            log.warning(
                "Failed to register for incoming RFCOMM channel notifications "
                "(channel ID %d). Connections will not be accepted.", RFCOMM_CHANNEL_ID
            )

    def _on_client_connected(self, channel):
        if not self._enforce_link_encryption(channel):
            log.warning("Link encryption check failed, rejecting connection")
            channel.closeChannel()
            return

        with self._lock:
            self._channel = channel
        self._connected.set()

        if not self._first_pairing_done:
            self.set_non_discoverable()
            self._first_pairing_done = True

        if self._on_connect_callback:
            self._on_connect_callback()
        log.info("Client connected (encrypted)")

    def _on_client_disconnected(self):
        self._connected.clear()
        with self._lock:
            self._channel = None
        if self._on_disconnect_callback:
            self._on_disconnect_callback()
        log.info("Client disconnected, awaiting reconnection…")

    def _close_channel(self):
        with self._lock:
            ch = self._channel
            self._channel = None
        if ch:
            try:
                ch.closeChannel()
            except Exception:
                pass
        self._connected.clear()
