# drone_controller.py
"""
Thin wrapper around pymavlink for the follow-me controller.
Handles connecting, mode changes, arming, and sending position targets.

This is the cleaned-up, consolidated version of the file you built step by
step in the earlier chat (all nine methods, with the two bugs you found
already fixed: home_lon naming, and RTN -> RTL).
"""

import time

from pymavlink import mavutil

import config


class DroneController:
    def __init__(self):
        self.master = None
        self.home_lat = None
        self.home_lon = None
        self.last_heartbeat_time = 0.0

    def connect(self):
        print(f"Connecting to {config.MAVLINK_CONNECTION} ...")
        self.master = mavutil.mavlink_connection(
            config.MAVLINK_CONNECTION, baud=config.MAVLINK_BAUD
        )
        self.master.wait_heartbeat()
        self.last_heartbeat_time = time.time()
        print(
            f"Heartbeat received (system {self.master.target_system}, "
            f"component {self.master.target_component})"
        )

    def get_position(self):
        """Returns (lat, lon, relative_alt_m) or None if no fresh message yet."""
        msg = self.master.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
        if msg is None:
            return None
        return msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0

    def record_home_position(self):
        msg = self.master.recv_match(
            type="GLOBAL_POSITION_INT", blocking=True, timeout=5
        )
        if msg is None:
            raise RuntimeError("No GPS fix received from flight controller yet.")
        self.home_lat = msg.lat / 1e7
        self.home_lon = msg.lon / 1e7
        print(f"Home position recorded: {self.home_lat}, {self.home_lon}")

    def link_is_alive(self):
        msg = self.master.recv_match(type="HEARTBEAT", blocking=False)
        if msg is not None:
            self.last_heartbeat_time = time.time()
        return (time.time() - self.last_heartbeat_time) < config.MAVLINK_LINK_TIMEOUT_S

    def set_mode_guided(self):
        mode_id = self.master.mode_mapping()["GUIDED"]
        self.master.mav.set_mode_send(
            self.master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
        )

    def arm(self):
        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1, 0, 0, 0, 0, 0, 0,
        )
        self.master.motors_armed_wait()
        print("Armed.")

    def goto_position(self, lat, lon, alt_m):
        self.master.mav.set_position_target_global_int_send(
            0,
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,
            int(lat * 1e7),
            int(lon * 1e7),
            alt_m,
            0, 0, 0,
            0, 0, 0,
            0, 0,
        )

    def trigger_failsafe(self):
        mode = "RTL" if config.FAILSAFE_ACTION == "RTL" else "LAND"
        print(f"FAILSAFE TRIGGERED -> switching to {mode}")
        mode_id = self.master.mode_mapping()[mode]
        self.master.mav.set_mode_send(
            self.master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
        )
