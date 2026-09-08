from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from pymavlink import mavutil


HEARTBEAT_TIMEOUT_S = 3.0
GPS_TIMEOUT_S = 3.0
RC_TIMEOUT_S = 2.0

MIN_GPS_FIX_TYPE = 3
MIN_GPS_SATELLITES = 6
MIN_RC_CHANNELS = 4

SAFETY_CHANNEL = 9
SAFETY_ACTIVE_THRESHOLD = 1800
SAFETY_CONFIRM_SAMPLES = 3


class SafetyDecision(Enum):
    ALLOW = "ALLOW"
    BLOCK_START = "BLOCK_START"
    STOP_PROGRAM = "STOP_PROGRAM"


@dataclass
class SafetyState:
    mavlink_ok: bool = False
    heartbeat_age_s: float | None = None

    armed: bool = False
    mode: str = "UNKNOWN"

    gps_ok: bool = False
    gps_fix_type: int | None = None
    gps_satellites: int | None = None
    gps_age_s: float | None = None

    latitude: float | None = None
    longitude: float | None = None

    rc_ok: bool = False
    rc_age_s: float | None = None
    rc_channel_count: int | None = None

    safety_channel_value: int | None = None
    stop_program_latched: bool = False


class SafetyMonitor:
    """
    Pure safety-state evaluator.

    IMPORTANT:
    - Opens NO serial port.
    - Sends NO MAVLink commands.
    - Sends NO LAND / ARM / DISARM / GOTO / throttle / motor command.
    - It only consumes MAVLink messages supplied by DroneController.
    """

    def __init__(self):
        self.state = SafetyState()

        self.last_heartbeat_time = None
        self.last_gps_time = None
        self.last_rc_time = None

        self._stop_confirm_count = 0

    def process_message(self, msg):
        now = time.monotonic()
        msg_type = msg.get_type()

        if msg_type == "HEARTBEAT":
            if msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
                return

            self.last_heartbeat_time = now
            self.state.armed = bool(
                msg.base_mode
                & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            )
            self.state.mode = mavutil.mode_string_v10(msg)

        elif msg_type == "GPS_RAW_INT":
            self.last_gps_time = now
            self.state.gps_fix_type = int(msg.fix_type)
            self.state.gps_satellites = int(msg.satellites_visible)

        elif msg_type == "GLOBAL_POSITION_INT":
            self.state.latitude = msg.lat / 1e7
            self.state.longitude = msg.lon / 1e7

        elif msg_type == "RC_CHANNELS":
            self.last_rc_time = now
            self.state.rc_channel_count = int(msg.chancount)

            value = getattr(
                msg,
                f"chan{SAFETY_CHANNEL}_raw",
                None,
            )

            if value in (0, 65535):
                value = None

            self.state.safety_channel_value = value
            self._process_stop_channel(value)

    def _process_stop_channel(self, value):
        if self.state.stop_program_latched:
            return

        if value is None:
            self._stop_confirm_count = 0
            return

        if value >= SAFETY_ACTIVE_THRESHOLD:
            self._stop_confirm_count += 1

            if self._stop_confirm_count >= SAFETY_CONFIRM_SAMPLES:
                self.state.stop_program_latched = True
        else:
            self._stop_confirm_count = 0

    def evaluate(self):
        now = time.monotonic()
        reasons = []

        # Highest priority: CH9 permanently stops this autonomy session.
        if self.state.stop_program_latched:
            return (
                SafetyDecision.STOP_PROGRAM,
                [f"RC Emergency Stop CH{SAFETY_CHANNEL} ausgelöst"],
            )

        # Flight-controller heartbeat.
        if self.last_heartbeat_time is None:
            self.state.mavlink_ok = False
            self.state.heartbeat_age_s = None
            reasons.append("Kein Flight-Controller-Heartbeat")
        else:
            age = now - self.last_heartbeat_time
            self.state.heartbeat_age_s = age
            self.state.mavlink_ok = age <= HEARTBEAT_TIMEOUT_S

            if not self.state.mavlink_ok:
                reasons.append(
                    f"Flight-Controller-Heartbeat zu alt ({age:.1f}s)"
                )

        # Flight-controller GPS.
        if self.last_gps_time is None:
            self.state.gps_ok = False
            self.state.gps_age_s = None
            reasons.append("Keine FC-GPS-Daten")
        else:
            age = now - self.last_gps_time
            self.state.gps_age_s = age

            if age > GPS_TIMEOUT_S:
                self.state.gps_ok = False
                reasons.append(f"FC-GPS-Daten zu alt ({age:.1f}s)")
            elif (
                self.state.gps_fix_type is None
                or self.state.gps_fix_type < MIN_GPS_FIX_TYPE
            ):
                self.state.gps_ok = False
                reasons.append(
                    f"FC-GPS Fix zu schlecht (fix={self.state.gps_fix_type})"
                )
            elif (
                self.state.gps_satellites is None
                or self.state.gps_satellites < MIN_GPS_SATELLITES
            ):
                self.state.gps_ok = False
                reasons.append(
                    f"Zu wenige Satelliten ({self.state.gps_satellites})"
                )
            elif (
                self.state.latitude is None
                or self.state.longitude is None
            ):
                self.state.gps_ok = False
                reasons.append("Keine FC-Position")
            elif (
                abs(self.state.latitude) < 0.000001
                and abs(self.state.longitude) < 0.000001
            ):
                self.state.gps_ok = False
                reasons.append("Ungültige FC-Position 0,0")
            else:
                self.state.gps_ok = True

        # RC / ELRS.
        if self.last_rc_time is None:
            self.state.rc_ok = False
            self.state.rc_age_s = None
            reasons.append("Keine RC/ELRS-Daten")
        else:
            age = now - self.last_rc_time
            self.state.rc_age_s = age

            if age > RC_TIMEOUT_S:
                self.state.rc_ok = False
                reasons.append(f"RC/ELRS-Daten zu alt ({age:.1f}s)")
            elif (
                self.state.rc_channel_count is None
                or self.state.rc_channel_count < MIN_RC_CHANNELS
            ):
                self.state.rc_ok = False
                reasons.append("Zu wenige RC-Kanäle erkannt")
            elif self.state.safety_channel_value is None:
                self.state.rc_ok = False
                reasons.append(f"Safety-Kanal CH{SAFETY_CHANNEL} fehlt")
            else:
                self.state.rc_ok = True

        if (
            self.state.mavlink_ok
            and self.state.gps_ok
            and self.state.rc_ok
        ):
            return SafetyDecision.ALLOW, []

        return SafetyDecision.BLOCK_START, reasons
