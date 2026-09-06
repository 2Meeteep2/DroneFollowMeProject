import json
import math
import ssl
import threading
import time
from collections import deque
from http.server import HTTPServer, SimpleHTTPRequestHandler

import config
import geo_utils

# ========================================
# FARBEN
# ========================================
RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
VIOLET = "\033[95m"
CYAN = "\033[96m"

# ========================================
# VERBINDUNG / FAILSAFE
# ========================================
LINK_TIMEOUT_S = 5.0
GPS_FIX_TIMEOUT_S = 15.0

# ========================================
# GPS-FILTER
# ========================================
FILTER_WINDOW_SIZE = 5
GOOD_ACCURACY_M = 8.0
MAX_ACCEPTABLE_ACCURACY_M = 15.0

# ========================================
# BEWEGUNGSERKENNUNG
# ========================================
STATIONARY_SPEED_MPS = 0.3
MOVING_SPEED_MPS = 0.7
STATIONARY_DEADBAND_M = 0.8
MOVING_DEADBAND_M = 0.6
STATIONARY_CONFIRMATIONS = 2
MOVING_CONFIRMATIONS = 1
IMMEDIATE_MOVE_M = 2.0

# ========================================
# START-STABILISIERUNG
# ========================================
STARTUP_MAX_ACCURACY_M = 6.5
STARTUP_MAX_SPREAD_M = 1.2
STARTUP_MIN_WAIT_S = 8.0
STARTUP_STABLE_HOLD_S = 3.0
STARTUP_TIMEOUT_S = 45.0
STARTUP_REQUIRED_SAMPLES = 5

# ========================================
# LOOP-RATE
# ========================================
CONTROL_INTERVAL_S = 0.25  # 4 Hz


class GPSState:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_packet = None
        self.filtered_position = None
        self.history = deque(maxlen=FILTER_WINDOW_SIZE)
        self.last_fix_id = None
        self.filter_seq = 0
        self.last_accepted_fix_origin_time = None

    @staticmethod
    def _point_weight(point):
        accuracy = max(float(point["accuracy"]), 1.0)

        if accuracy <= GOOD_ACCURACY_M:
            return 1.0 / (accuracy * accuracy)

        # 8-15 m: noch verwenden, aber deutlich schwächer gewichten.
        return 0.25 / (accuracy * accuracy)

    @classmethod
    def _weighted_average(cls, points):
        weighted_lat = 0.0
        weighted_lon = 0.0
        total_weight = 0.0

        for point in points:
            weight = cls._point_weight(point)
            weighted_lat += point["lat"] * weight
            weighted_lon += point["lon"] * weight
            total_weight += weight

        if total_weight <= 0:
            return None

        return (
            weighted_lat / total_weight,
            weighted_lon / total_weight,
        )

    def update_packet(self, data):
        now = time.time()

        lat = float(data["lat"])
        lon = float(data["lon"])
        accuracy = float(data["accuracy"])
        fix_age_ms = max(0.0, float(data["fixAgeMs"]))
        fix_id = int(data["fixId"])

        speed_value = data.get("speed")
        heading_value = data.get("heading")

        speed = None if speed_value is None else float(speed_value)
        heading = None if heading_value is None else float(heading_value)

        if not (-90.0 <= lat <= 90.0):
            raise ValueError("Ungültiger Breitengrad")

        if not (-180.0 <= lon <= 180.0):
            raise ValueError("Ungültiger Längengrad")

        if not math.isfinite(accuracy) or accuracy < 0:
            raise ValueError("Ungültige GPS-Genauigkeit")

        if fix_age_ms > 3_600_000:
            raise ValueError("Unplausibles GPS-Fix-Alter")

        if speed is not None and not math.isfinite(speed):
            speed = None

        if heading is not None and not math.isfinite(heading):
            heading = None

        packet = {
            "lat": lat,
            "lon": lon,
            "accuracy": accuracy,
            "speed": speed,
            "heading": heading,
            "fix_age_s": fix_age_ms / 1000.0,
            "fix_id": fix_id,
            "received_at": now,
        }

        with self.lock:
            # Jedes Paket hält die iPhone-Verbindung am Leben.
            self.latest_packet = packet

            # Gleiches fixId = derselbe echte GPS-Fix erneut gesendet.
            if fix_id == self.last_fix_id:
                return

            self.last_fix_id = fix_id

            # Extrem ungenau: Verbindung lebt, aber Position nicht übernehmen.
            if accuracy > MAX_ACCEPTABLE_ACCURACY_M:
                print(
                    YELLOW
                    + f"[FILTER] GPS zu ungenau: {accuracy:.1f} m "
                    + "-> letzte gute Position wird gehalten"
                    + RESET
                )
                return

            point = {
                "lat": lat,
                "lon": lon,
                "accuracy": accuracy,
                "fix_id": fix_id,
            }
            self.history.append(point)

            filtered = self._weighted_average(self.history)
            if filtered is None:
                return

            self.filtered_position = {
                "lat": filtered[0],
                "lon": filtered[1],
                "accuracy": accuracy,
                "speed": speed,
                "fix_id": fix_id,
                "samples": len(self.history),
            }

            self.filter_seq += 1
            self.last_accepted_fix_origin_time = now - fix_age_ms / 1000.0

    def snapshot(self):
        with self.lock:
            packet = None if self.latest_packet is None else dict(self.latest_packet)
            filtered = (
                None
                if self.filtered_position is None
                else dict(self.filtered_position)
            )
            history = [dict(point) for point in self.history]

            return {
                "packet": packet,
                "filtered": filtered,
                "history": history,
                "filter_seq": self.filter_seq,
                "accepted_fix_origin_time": self.last_accepted_fix_origin_time,
            }


gps_state = GPSState()


class TestHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/gps":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length)
            data = json.loads(raw.decode("utf-8"))

            gps_state.update_packet(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        except Exception as error:
            print(RED + f"[SERVER] Ungültiges GPS-Paket: {error}" + RESET)
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            response = json.dumps({"ok": False, "error": str(error)})
            self.wfile.write(response.encode("utf-8"))

    def log_message(self, format_string, *args):
        # Erfolgreiche 200er nicht ständig ausgeben.
        if args and str(args[1]) != "200":
            super().log_message(format_string, *args)


class FakeDroneController:
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
            + "[DRONE] Home gespeichert: "
            + f"{lat:.6f}, {lon:.6f}"
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

    def trigger_failsafe(self, reason):
        print(RED + "[SAFETY] " + reason + RESET)
        print(
            RED
            + "[DRONE] FAILSAFE -> "
            + str(config.FAILSAFE_ACTION)
            + RESET
        )


def start_https_server():
    server = HTTPServer(("0.0.0.0", 8000), TestHandler)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("cert.pem", "key.pem")

    server.socket = context.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(CYAN + "[SERVER] HTTPS-Webserver + GPS läuft auf Port 8000" + RESET)


def history_spread_m(history):
    if len(history) < 2:
        return float("inf")

    center = GPSState._weighted_average(history)
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


def wait_for_stable_gps(drone):
    print()
    print(
        YELLOW
        + "[START] GPS wird zuerst stabilisiert. Noch kein ARM/GOTO."
        + RESET
    )

    started_at = time.time()
    stable_since = None

    while True:
        now = time.time()
        elapsed = now - started_at
        snapshot = gps_state.snapshot()

        packet = snapshot["packet"]
        filtered = snapshot["filtered"]
        history = snapshot["history"]
        accepted_fix_origin_time = snapshot["accepted_fix_origin_time"]

        if packet is None:
            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | Warte auf ersten GPS-Fix ..."
                + RESET,
                end="\r",
                flush=True,
            )
            time.sleep(0.25)
            continue

        packet_age = now - packet["received_at"]

        if packet_age > LINK_TIMEOUT_S:
            print()
            drone.trigger_failsafe("Verbindung zum iPhone beim Start verloren!")
            return None

        if elapsed > STARTUP_TIMEOUT_S:
            print()
            drone.trigger_failsafe(
                "GPS wurde innerhalb von 30 s nicht stabil genug. Start abgebrochen!"
            )
            return None

        if filtered is None or accepted_fix_origin_time is None:
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

        accepted_fix_age = now - accepted_fix_origin_time
        spread = history_spread_m(history)
        samples = len(history)

        conditions_ok = (
            elapsed >= STARTUP_MIN_WAIT_S
            and samples >= STARTUP_REQUIRED_SAMPLES
            and packet["accuracy"] <= STARTUP_MAX_ACCURACY_M
            and spread <= STARTUP_MAX_SPREAD_M
            and accepted_fix_age <= GPS_FIX_TIMEOUT_S
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
                + f"stabil {stable_for:.1f}/{STARTUP_STABLE_HOLD_S:.1f}s"
                + RESET,
                end="\r",
                flush=True,
            )

            if stable_for >= STARTUP_STABLE_HOLD_S:
                print()
                print(
                    GREEN
                    + "[START] GPS stabil -> Follow-Me darf starten."
                    + RESET
                )
                return filtered

        else:
            stable_since = None
            reasons = []

            if elapsed < STARTUP_MIN_WAIT_S:
                reasons.append(
                    f"Delay {elapsed:.1f}/{STARTUP_MIN_WAIT_S:.1f}s"
                )

            if samples < STARTUP_REQUIRED_SAMPLES:
                reasons.append(
                    f"Fixes {samples}/{STARTUP_REQUIRED_SAMPLES}"
                )

            if packet["accuracy"] > STARTUP_MAX_ACCURACY_M:
                reasons.append(
                    f"Accuracy {packet['accuracy']:.1f}>{STARTUP_MAX_ACCURACY_M:.1f}m"
                )

            if spread > STARTUP_MAX_SPREAD_M:
                if math.isfinite(spread):
                    reasons.append(
                        f"Spread {spread:.2f}>{STARTUP_MAX_SPREAD_M:.2f}m"
                    )
                else:
                    reasons.append("Spread noch unbekannt")

            print(
                YELLOW
                + f"[START] {elapsed:4.1f}s | "
                + " | ".join(reasons)
                + RESET,
                end="\r",
                flush=True,
            )

        time.sleep(0.25)


def movement_mode(speed):
    if speed is None:
        return "UNKNOWN"

    if speed >= MOVING_SPEED_MPS:
        return "MOVING"

    if speed <= STATIONARY_SPEED_MPS:
        return "STILL"

    return "TRANSITION"


def main():
    print()
    print("=== LIVE GPS LOCAL TEST ===")
    print("Keine echte Drohne wird angesprochen.")
    print()

    start_https_server()

    drone = FakeDroneController()
    drone.connect()

    stable_position = wait_for_stable_gps(drone)
    if stable_position is None:
        return

    drone.record_home_position(
        stable_position["lat"],
        stable_position["lon"],
    )
    drone.set_mode_guided()
    drone.arm()

    last_commanded_target = None
    last_seen_filter_seq = -1
    movement_confirmations = 0

    print()
    print(GREEN + "[SYSTEM] Follow-Me läuft." + RESET)
    print("[SYSTEM] Mit Strg+C beenden.")
    print()

    try:
        while True:
            snapshot = gps_state.snapshot()

            packet = snapshot["packet"]
            filtered = snapshot["filtered"]
            filter_seq = snapshot["filter_seq"]
            accepted_fix_origin_time = snapshot["accepted_fix_origin_time"]

            if (
                packet is None
                or filtered is None
                or accepted_fix_origin_time is None
            ):
                time.sleep(CONTROL_INTERVAL_S)
                continue

            now = time.time()
            packet_age = now - packet["received_at"]
            accepted_fix_age = now - accepted_fix_origin_time

            if packet_age > LINK_TIMEOUT_S:
                drone.trigger_failsafe("Verbindung zum iPhone verloren!")
                break

            if accepted_fix_age > GPS_FIX_TIMEOUT_S:
                drone.trigger_failsafe(
                    "Seit 15 s kein ausreichend genauer GPS-Fix mehr!"
                )
                break

            phone_lat = filtered["lat"]
            phone_lon = filtered["lon"]

            dist_from_home = geo_utils.distance_m(
                drone.home_lat,
                drone.home_lon,
                phone_lat,
                phone_lon,
            )

            if dist_from_home > config.MAX_RADIUS_FROM_HOME_M:
                drone.trigger_failsafe("Geofence überschritten!")
                break

            target_lat, target_lon = geo_utils.offset_point(
                phone_lat,
                phone_lon,
                config.FOLLOW_BEARING_DEG,
                config.FOLLOW_OFFSET_M,
            )

            speed = packet.get("speed")
            mode = movement_mode(speed)

            if mode == "MOVING":
                deadband = MOVING_DEADBAND_M
                confirmations_needed = MOVING_CONFIRMATIONS
            else:
                deadband = STATIONARY_DEADBAND_M
                confirmations_needed = STATIONARY_CONFIRMATIONS

            speed_text = "–" if speed is None else f"{speed:.2f} m/s"

            print(
                f"[GPS] accuracy={packet['accuracy']:.1f} m | "
                f"speed={speed_text} | "
                f"mode={mode} | "
                f"Home={dist_from_home:.2f} m | "
                f"packet_age={packet_age:.1f}s | "
                f"fix_age={accepted_fix_age:.1f}s"
            )

            if last_commanded_target is None:
                print(
                    GREEN
                    + "[TARGET] POSITION VERÄNDERT -> erstes GOTO"
                    + RESET
                )
                drone.goto_position(
                    target_lat,
                    target_lon,
                    config.FOLLOW_ALTITUDE_M,
                )
                last_commanded_target = (target_lat, target_lon)
                last_seen_filter_seq = filter_seq
                movement_confirmations = 0
                print()
                time.sleep(CONTROL_INTERVAL_S)
                continue

            target_change = geo_utils.distance_m(
                target_lat,
                target_lon,
                last_commanded_target[0],
                last_commanded_target[1],
            )

            # Nur bei einem echten neuen akzeptierten GPS-Fix auswerten.
            if filter_seq != last_seen_filter_seq:
                last_seen_filter_seq = filter_seq

                if target_change >= IMMEDIATE_MOVE_M:
                    print(
                        GREEN
                        + "[TARGET] POSITION VERÄNDERT "
                        + f"({target_change:.2f} m) -> sofort neues GOTO"
                        + RESET
                    )
                    drone.goto_position(
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
                        + "[TARGET] mögliche Bewegung "
                        + f"{target_change:.2f} m | "
                        + f"mode={mode} | "
                        + "Bestätigung "
                        + f"{movement_confirmations}/{confirmations_needed}"
                        + RESET
                    )

                    if movement_confirmations >= confirmations_needed:
                        print(
                            GREEN
                            + "[TARGET] POSITION VERÄNDERT -> neues GOTO"
                            + RESET
                        )
                        drone.goto_position(
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
                        + "[TARGET] POSITION STABIL "
                        + f"({target_change:.2f} m < {deadband:.2f} m) "
                        + "-> kein neues GOTO"
                        + RESET
                    )

            else:
                print(
                    VIOLET
                    + "[TARGET] POSITION NICHT AKTUALISIERT "
                    + "(kein neuer echter GPS-Fix)"
                    + RESET
                )

            print()
            time.sleep(CONTROL_INTERVAL_S)

    except KeyboardInterrupt:
        print()
        drone.trigger_failsafe("STOP manuell ausgelöst.")


if __name__ == "__main__":
    main()
