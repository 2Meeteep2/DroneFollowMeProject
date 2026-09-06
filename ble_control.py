# ble_control.py
"""
Bluetooth LE peripheral running on the Pi. Your laptop (Chrome/Edge, via the
Web Bluetooth API in control.html) connects to this and writes text commands
("START", "STOP", "ARM") to the control characteristic.

Uses the 'bluezero' library, which talks to BlueZ over D-Bus - this is the
standard way to build a BLE GATT peripheral on a Raspberry Pi.

Install once on the Pi:
    sudo apt-get install -y bluetooth bluez python3-dbus
    pip install bluezero --break-system-packages   # inside your venv: no flag needed

Runs its own GLib event loop, so we start it in a background thread from
follow_me.py and read the command via BLECommandLink.latest_command.
"""

import threading

from bluezero import adapter, peripheral

import config


class BLECommandLink:
    def __init__(self):
        self._lock = threading.Lock()
        self._command = None
        self._peripheral = None

    def _on_write(self, value, options):
        try:
            text = bytes(value).decode("utf-8").strip().upper()
        except UnicodeDecodeError:
            return
        with self._lock:
            self._command = text
        print(f"[BLE] received command: {text}")

    def take_command(self):
        """Returns the latest command and clears it (so it's only acted on once)."""
        with self._lock:
            cmd, self._command = self._command, None
            return cmd

    def notify_status(self, text):
        """Push a short status string back to the laptop, if it's listening."""
        if self._peripheral is None:
            return
        try:
            char = self._peripheral.characteristics[
                (1, config.BLE_STATUS_CHAR_UUID)
            ]
            char.set_value(list(text.encode("utf-8")))
        except Exception:
            pass  # best-effort only; never let a UI update crash the flight loop

    def run(self):
        """Blocking call - run this in a background thread."""
        addr = list(adapter.Adapter.available())[0].address
        dev = peripheral.Peripheral(addr, local_name=config.BLE_LOCAL_NAME)

        dev.add_service(srv_id=1, uuid=config.BLE_SERVICE_UUID, primary=True)

        dev.add_characteristic(
            srv_id=1,
            chr_id=1,
            uuid=config.BLE_CTRL_CHAR_UUID,
            value=[],
            notifying=False,
            flags=["write", "write-without-response"],
            write_callback=self._on_write,
        )

        dev.add_characteristic(
            srv_id=1,
            chr_id=2,
            uuid=config.BLE_STATUS_CHAR_UUID,
            value=list(b"idle"),
            notifying=True,
            flags=["read", "notify"],
        )

        self._peripheral = dev
        print(f"[BLE] advertising as '{config.BLE_LOCAL_NAME}' ...")
        dev.publish()  # blocks forever, runs the GLib main loop


def start_ble_link():
    """Creates the link and starts it in a background thread. Returns the link."""
    link = BLECommandLink()
    thread = threading.Thread(target=link.run, daemon=True)
    thread.start()
    return link
