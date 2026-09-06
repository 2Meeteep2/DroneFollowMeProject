# follow_me.py
"""
Main Follow-Me controller.

TEST_MODE = True:
  - real iPhone GPS via gps_server.py
  - simulated drone
  - START / STOP entered in the console
  - safe to run locally on Windows

TEST_MODE = False:
  - real Raspberry Pi BLE control
  - real DroneController / MAVLink
"""

from __future__ import annotations

import math
import queue
import threading
import time

import config
import geo_utils
from gps_server import phone_gps, start_server


RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
VIOLET = "\033[95m"
CYAN = "\033[96m"


class ConsoleCommandLink:
    """Local replacement for the Raspberry Pi BLE link."""

    def __init__(self):
        self._commands = queue.Queue()
        self._thread = threading.Thread(target=self._input_loop, daemon=True)
        self._thread.start()

    def _input_loop(self):
        print(CYAN + "[CONTROL] Tippe START oder STOP und drücke Enter." + RESET)
        while True:
            try:
                text = input().strip().upper()
            except (EOFError, KeyboardInterrupt):
                return

            if text in ("START", "STOP"):
                self._commands.put(text)
            elif text:
                print(YELLOW + "[CONTROL] Erlaubt: START oder STOP" + RESET)

    def take_command(self):
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def notify_status(self, text):
        print(CYAN + f"[STATUS] {text}" + RESET)


class SimulatedDroneController:
    """Local replacement for DroneController. Sends no MAVLink."""

    def __init__(self):
        self.home_lat = None
        self.home_lon = None

    def connect(self):
        print(CYAN + "[DRONE] Simulierte Verbindung hergestellt." + RESET)

    def record_home_position(self, lat, lon):
        self.home_lat = lat
        self.home_lon = lon
        print(
            CYAN
            + f"[DRONE] Home gespeichert: {lat:.6f}, {lon:.6f}"
            + RESET
        )

    def set_mode_guided(self):
        print(CYAN + "[DRONE] Modus -> GUIDED" + RESET)

    def arm(self):
        print(CYAN + "[DRONE] ARM" + RESET)

    def goto_position(self, lat, lon, alt_m):
        print(
            GREEN
            + "[DRONE] GOTO -> "
            + f"lat={lat:.7f}, lon={lon:.7f}, alt={alt_m:.1f} m"
            + RESET
        )

    def link_is_alive(self):
        return True

    def trigger_failsafe(self):
        print(
            RED
            + "[DRONE] FAILSAFE -> "
            + str(config.FAILSAFE_ACTION)
            + RESET
        )


def build_hardware():
    if config.TEST_MODE:
        print(YELLOW + "[SYSTEM] TEST_MODE aktiv - keine echte Drohne." + RESET)
        return SimulatedDroneController(), ConsoleCommandLink()

    # Import only on the Raspberry Pi. This keeps Windows test mode free
    # from bluezero / BlueZ dependencies.
    from ble_control import start_ble_link
    from drone_controller import DroneController

    controller = DroneController()
    ble_link = start_ble_link()
    return controller, ble_link


def weighted_history_center(history):
    weighted_lat = 0.0
    weighted_lon = 0.0
    total_weight = 0.0

    for point in history:
        accuracy = max(float(point["accuracy"]), 1.0)
        if accuracy <= config.PHONE_GPS_GOOD_ACCURACY_M:
            weight = 1.0 / (accuracy * accuracy)
        else:
            weight = 0.25 / (accuracy * accuracy)

        weighted_lat += point["lat"] * weight
        weighted_lon += point["lon"] * weight
        total_weight += weight

    if total_weight <= 0.0:
        return None

    return weighted_lat / total_weight, weighted_lon / total_weight


def history_spread_m(history):
    if len(history) < 2:
        return float("inf")

    center = weighted_history_center(history)
    if center is None:
        return float("inf")

    center_lat, center_lon = center

    return max(
        geo_utils.distance_m(
            center_lat,
            center_lon,
            point["lat"],
            point["lon"],
        )
        for point in history
    )


def movement_mode(speed):
    if speed is None:
        return "UNKNOWN"

    if speed >= config.MOVING_SPEED_MPS:
        return "MOVING"

    if speed <= config.STATIONARY_SPEED_MPS:
        return "STILL"

    return "TRANSITION"


def wait_for_start(command_link):
    command_link.notify_status("waiting")
    print(CYAN + "[SYSTEM] Warte auf START ..." + RESET)

    while True:
        command = command_link.take_command()

        if command == "START":
            print(GREEN + "[CONTROL] START empfangen." + RESET)
            return True

        if command == "STOP":
            print(YELLOW + "[CONTROL] STOP ignoriert: noch nicht gestartet." + RESET)

        time.sleep(0.1)


def wait_for_stable_phone_gps(command_link, controller):
    print(
        YELLOW
        + "[START] GPS wird stabilisiert. Noch kein ARM/GOTO."
        + RESET
    )

    started_at = time.time()
    stable_since = None

    while True:
        now = time.time()
        elapsed = now - started_at

        command = command_link.take_command()
        if command == "STOP":
            print(YELLOW + "[START] Start durch STOP abgebrochen." + RESET)
            command_link.notify_status("cancelled")
            return None

        if not controller.link_is_alive():
            print(RED + "[SAFETY] Flight-Controller-Link verloren." + RESET)
            return None

        snapshot = phone_gps.snapshot()
        packet = snapshot["packet"]
        filtered = snapshot["filtered"]
        history = snapshot["history"]
        packet_age = snapshot["packet_age"]
        good_fix_age = snapshot["good_fix_age"]

        if packet is None:
            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | warte auf GPS ..."
                + RESET,
                end="\r",
                flush=True,
            )
            time.sleep(0.25)
            continue

        if packet_age is not None and packet_age > config.PHONE_LINK_TIMEOUT_S:
            print()
            print(RED + "[SAFETY] iPhone-Verbindung beim Start verloren." + RESET)
            command_link.notify_status("phone_lost")
            return None

        if elapsed > config.STARTUP_TIMEOUT_S:
            print()
            print(
                RED
                + "[START] GPS wurde nicht rechtzeitig stabil genug."
                + RESET
            )
            command_link.notify_status("gps_unstable")
            return None

        if filtered is None or good_fix_age is None:
            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | "
                + f"accuracy={packet['accuracy']:.1f} m | "
                + "warte auf brauchbare Fixes ..."
                + RESET,
                end="\r",
                flush=True,
            )
            time.sleep(0.25)
            continue

        samples = len(history)
        spread = history_spread_m(history)

        conditions_ok = (
            elapsed >= config.STARTUP_MIN_WAIT_S
            and samples >= config.STARTUP_REQUIRED_SAMPLES
            and packet["accuracy"] <= config.STARTUP_MAX_ACCURACY_M
            and spread <= config.STARTUP_MAX_SPREAD_M
            and good_fix_age <= config.PHONE_GPS_FIX_TIMEOUT_S
        )

        if conditions_ok:
            if stable_since is None:
                stable_since = now

            stable_for = now - stable_since

            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | "
                + f"accuracy={packet['accuracy']:.1f} m | "
                + f"spread={spread:.2f} m | "
                + f"stabil {stable_for:.1f}/{config.STARTUP_STABLE_HOLD_S:.1f}s"
                + RESET,
                end="\r",
                flush=True,
            )

            if stable_for >= config.STARTUP_STABLE_HOLD_S:
                print()
                print(GREEN + "[START] GPS stabil." + RESET)
                return dict(filtered)

        else:
            stable_since = None
            reasons = []

            if elapsed < config.STARTUP_MIN_WAIT_S:
                reasons.append(
                    f"Delay {elapsed:.1f}/{config.STARTUP_MIN_WAIT_S:.1f}s"
                )

            if samples < config.STARTUP_REQUIRED_SAMPLES:
                reasons.append(
                    f"Fixes {samples}/{config.STARTUP_REQUIRED_SAMPLES}"
                )

            if packet["accuracy"] > config.STARTUP_MAX_ACCURACY_M:
                reasons.append(
                    f"Accuracy {packet['accuracy']:.1f}>{config.STARTUP_MAX_ACCURACY_M:.1f}m"
                )

            if spread > config.STARTUP_MAX_SPREAD_M:
                if math.isfinite(spread):
                    reasons.append(
                        f"Spread {spread:.2f}>{config.STARTUP_MAX_SPREAD_M:.2f}m"
                    )
                else:
                    reasons.append("Spread unbekannt")

            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | "
                + " | ".join(reasons)
                + RESET,
                end="\r",
                flush=True,
            )

        time.sleep(0.25)


def follow_loop(controller, command_link):
    last_commanded_target = None
    last_seen_filter_seq = -1
    movement_confirmations = 0

    command_link.notify_status("following")
    print(GREEN + "[SYSTEM] Follow-Me läuft." + RESET)

    interval = 1.0 / max(config.UPDATE_RATE_HZ, 0.1)

    while True:
        started = time.time()

        if not controller.link_is_alive():
            print(RED + "[SAFETY] Flight-Controller-Link verloren." + RESET)
            controller.trigger_failsafe()
            return

        command = command_link.take_command()
        if command == "STOP":
            print(RED + "[CONTROL] STOP -> Failsafe." + RESET)
            controller.trigger_failsafe()
            return

        snapshot = phone_gps.snapshot()
        packet = snapshot["packet"]
        filtered = snapshot["filtered"]
        packet_age = snapshot["packet_age"]
        good_fix_age = snapshot["good_fix_age"]
        filter_seq = snapshot["filter_seq"]

        if packet is None or filtered is None:
            print(RED + "[SAFETY] GPS-Daten fehlen." + RESET)
            controller.trigger_failsafe()
            return

        if packet_age is None or packet_age > config.PHONE_LINK_TIMEOUT_S:
            print(RED + "[SAFETY] Verbindung zum iPhone verloren." + RESET)
            controller.trigger_failsafe()
            return

        if good_fix_age is None or good_fix_age > config.PHONE_GPS_FIX_TIMEOUT_S:
            print(RED + "[SAFETY] Kein ausreichend frischer GPS-Fix." + RESET)
            controller.trigger_failsafe()
            return

        phone_lat = filtered["lat"]
        phone_lon = filtered["lon"]

        if controller.home_lat is not None and controller.home_lon is not None:
            dist_from_home = geo_utils.distance_m(
                controller.home_lat,
                controller.home_lon,
                phone_lat,
                phone_lon,
            )

            if dist_from_home > config.MAX_RADIUS_FROM_HOME_M:
                print(
                    RED
                    + f"[SAFETY] Geofence überschritten: {dist_from_home:.1f} m"
                    + RESET
                )
                controller.trigger_failsafe()
                return
        else:
            dist_from_home = 0.0

        target_lat, target_lon = geo_utils.offset_point(
            phone_lat,
            phone_lon,
            config.FOLLOW_BEARING_DEG,
            config.FOLLOW_OFFSET_M,
        )

        speed = packet.get("speed")
        mode = movement_mode(speed)

        if mode == "MOVING":
            deadband = config.MOVING_DEADBAND_M
            confirmations_needed = config.MOVING_CONFIRMATIONS
        else:
            deadband = config.STATIONARY_DEADBAND_M
            confirmations_needed = config.STATIONARY_CONFIRMATIONS

        speed_text = "-" if speed is None else f"{speed:.2f} m/s"
        print(
            f"[GPS] accuracy={packet['accuracy']:.1f} m | "
            f"speed={speed_text} | mode={mode} | "
            f"Home={dist_from_home:.2f} m | fix_age={good_fix_age:.1f}s"
        )

        if last_commanded_target is None:
            print(GREEN + "[TARGET] erstes GOTO" + RESET)
            controller.goto_position(
                target_lat,
                target_lon,
                config.FOLLOW_ALTITUDE_M,
            )
            last_commanded_target = (target_lat, target_lon)
            last_seen_filter_seq = filter_seq
            movement_confirmations = 0

        else:
            target_change = geo_utils.distance_m(
                target_lat,
                target_lon,
                last_commanded_target[0],
                last_commanded_target[1],
            )

            if filter_seq != last_seen_filter_seq:
                last_seen_filter_seq = filter_seq

                if target_change >= config.IMMEDIATE_MOVE_M:
                    print(
                        GREEN
                        + f"[TARGET] POSITION VERÄNDERT ({target_change:.2f} m) -> GOTO"
                        + RESET
                    )
                    controller.goto_position(
                        target_lat,
                        target_lon,
                        config.FOLLOW_ALTITUDE_M,
                    )
                    last_commanded_target = (target_lat, target_lon)
                    movement_confirmations = 0

                elif target_change >= deadband:
                    movement_confirmations += 1
                    print(
                        YELLOW
                        + f"[TARGET] Bewegung {target_change:.2f} m | "
                        + f"Bestätigung {movement_confirmations}/{confirmations_needed}"
                        + RESET
                    )

                    if movement_confirmations >= confirmations_needed:
                        print(GREEN + "[TARGET] POSITION VERÄNDERT -> GOTO" + RESET)
                        controller.goto_position(
                            target_lat,
                            target_lon,
                            config.FOLLOW_ALTITUDE_M,
                        )
                        last_commanded_target = (target_lat, target_lon)
                        movement_confirmations = 0

                else:
                    movement_confirmations = 0
                    print(
                        VIOLET
                        + f"[TARGET] POSITION STABIL ({target_change:.2f} m)"
                        + RESET
                    )

            else:
                print(
                    VIOLET
                    + "[TARGET] kein neuer echter GPS-Fix"
                    + RESET
                )

        elapsed = time.time() - started
        time.sleep(max(0.0, interval - elapsed))


def run():
    start_server()
    controller, command_link = build_hardware()
    controller.connect()

    while True:
        wait_for_start(command_link)

        stable_position = wait_for_stable_phone_gps(command_link, controller)
        if stable_position is None:
            print(YELLOW + "[SYSTEM] Zurück zu START-Wartezustand." + RESET)
            continue

        if config.TEST_MODE:
            controller.record_home_position(
                stable_position["lat"],
                stable_position["lon"],
            )
        else:
            # Real hardware home = actual drone position before arming.
            controller.record_home_position()

        controller.set_mode_guided()
        controller.arm()
        command_link.notify_status("armed")

        follow_loop(controller, command_link)
        command_link.notify_status("stopped")

        print(YELLOW + "[SYSTEM] Follow-Me beendet." + RESET)
        break


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n" + YELLOW + "[SYSTEM] Manuell beendet." + RESET)