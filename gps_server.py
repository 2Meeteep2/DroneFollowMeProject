# gps_server.py
"""
HTTPS server for the iPhone Follow-Me GPS page.

The server does two things on the same HTTPS origin:

    GET / or /phone_gps.html
        -> serves the iPhone GPS page

    POST /gps
        -> receives GPS packets from the iPhone

Keeping the page and /gps on the same HTTPS origin avoids the
second-certificate / CORS problems that appeared during local Safari tests.

The GPS state also filters genuinely new GPS fixes. Re-sent heartbeat
packets keep the phone connection alive but are not counted repeatedly
inside the position filter.
"""

from __future__ import annotations

import json
import math
import ssl
import threading
import time

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config


class PhoneGPS:
    """Thread-safe GPS state shared with follow_me.py."""

    def __init__(self):
        self._lock = threading.Lock()

        self._latest_packet = None
        self._filtered = None

        self._history = deque(
            maxlen=config.PHONE_GPS_FILTER_WINDOW_SIZE
        )

        self._last_fix_id = None
        self._filter_seq = 0

        self._last_good_fix_origin_time = None


    @staticmethod
    def _weight(accuracy_m):
        accuracy_m = max(
            float(accuracy_m),
            1.0
        )

        if (
            accuracy_m
            <= config.PHONE_GPS_GOOD_ACCURACY_M
        ):
            return (
                1.0
                / (
                    accuracy_m
                    * accuracy_m
                )
            )

        # Fixes between 8 and 15 m
        # still count, but much less.
        return (
            0.25
            / (
                accuracy_m
                * accuracy_m
            )
        )


    @classmethod
    def _weighted_average(
        cls,
        points
    ):
        weighted_lat = 0.0
        weighted_lon = 0.0
        total_weight = 0.0

        for point in points:
            weight = cls._weight(
                point["accuracy"]
            )

            weighted_lat += (
                point["lat"]
                * weight
            )

            weighted_lon += (
                point["lon"]
                * weight
            )

            total_weight += weight

        if total_weight <= 0.0:
            return None

        return (
            weighted_lat / total_weight,
            weighted_lon / total_weight
        )


    def update(
        self,
        payload
    ):
        """
        Validate and store one GPS packet.

        Returns True only if this packet contains a NEW accepted GPS fix.

        Re-sent packets with the same fixId still refresh the phone-link
        timestamp, but do not enter the filter again.
        """

        now = time.time()

        lat = float(
            payload["lat"]
        )

        lon = float(
            payload["lon"]
        )

        accuracy = float(
            payload["accuracy"]
        )

        fix_age_ms = max(
            0.0,
            float(
                payload["fixAgeMs"]
            )
        )

        fix_id = int(
            payload["fixId"]
        )


        speed_raw = payload.get(
            "speed"
        )

        heading_raw = payload.get(
            "heading"
        )


        speed = (
            None
            if speed_raw is None
            else float(speed_raw)
        )

        heading = (
            None
            if heading_raw is None
            else float(heading_raw)
        )


        # -------------------------
        # Plausibility checks
        # -------------------------

        if (
            not math.isfinite(lat)
            or not -90.0 <= lat <= 90.0
        ):
            raise ValueError(
                "invalid latitude"
            )


        if (
            not math.isfinite(lon)
            or not -180.0 <= lon <= 180.0
        ):
            raise ValueError(
                "invalid longitude"
            )


        if (
            not math.isfinite(accuracy)
            or accuracy < 0.0
        ):
            raise ValueError(
                "invalid accuracy"
            )


        if (
            fix_age_ms
            > 3_600_000.0
        ):
            raise ValueError(
                "implausible fix age"
            )


        if (
            speed is not None
            and (
                not math.isfinite(speed)
                or speed < 0.0
            )
        ):
            speed = None


        if heading is not None:

            if not math.isfinite(
                heading
            ):
                heading = None

            else:
                heading %= 360.0


        packet = {
            "lat": lat,
            "lon": lon,
            "accuracy": accuracy,
            "speed": speed,
            "heading": heading,
            "fix_age_s":
                fix_age_ms / 1000.0,
            "fix_id": fix_id,
            "received_at": now
        }


        with self._lock:

            # Every packet is a heartbeat.
            self._latest_packet = packet


            # Same real GPS fix sent again:
            # do not add it to filter.
            if (
                fix_id
                == self._last_fix_id
            ):
                return False


            self._last_fix_id = (
                fix_id
            )


            # Very inaccurate position:
            # keep connection alive,
            # but hold last good position.
            if (
                accuracy
                > config
                .PHONE_GPS_MAX_ACCEPTABLE_ACCURACY_M
            ):
                return False


            point = {
                "lat": lat,
                "lon": lon,
                "accuracy": accuracy,
                "fix_id": fix_id
            }


            self._history.append(
                point
            )


            filtered = (
                self._weighted_average(
                    self._history
                )
            )


            if filtered is None:
                return False


            self._filter_seq += 1


            self._filtered = {
                "lat": filtered[0],
                "lon": filtered[1],
                "accuracy": accuracy,
                "speed": speed,
                "heading": heading,
                "fix_id": fix_id,
                "samples":
                    len(
                        self._history
                    )
            }


            # Convert relative iPhone
            # fix age into PC-local time.
            self._last_good_fix_origin_time = (
                now
                - (
                    fix_age_ms
                    / 1000.0
                )
            )


            return True


    def snapshot(self):
        """
        Return a copy of the state for follow_me.py.
        """

        with self._lock:

            packet = (
                None
                if self._latest_packet is None
                else dict(
                    self._latest_packet
                )
            )


            filtered = (
                None
                if self._filtered is None
                else dict(
                    self._filtered
                )
            )


            history = [
                dict(point)
                for point
                in self._history
            ]


            good_origin = (
                self._last_good_fix_origin_time
            )


            filter_seq = (
                self._filter_seq
            )


        now = time.time()


        packet_age = (
            None
            if packet is None
            else max(
                0.0,
                now
                - packet["received_at"]
            )
        )


        good_fix_age = (
            None
            if good_origin is None
            else max(
                0.0,
                now
                - good_origin
            )
        )


        return {
            "packet": packet,
            "filtered": filtered,
            "history": history,
            "filter_seq":
                filter_seq,
            "packet_age":
                packet_age,
            "good_fix_age":
                good_fix_age
        }


    @property
    def latest(self):
        """
        Backwards-compatible interface for the old follow_me.py.

        Returns:
            (filtered_lat, filtered_lon, packet_age)

        or None.
        """

        snap = self.snapshot()

        if (
            snap["filtered"]
            is None
            or snap["packet_age"]
            is None
        ):
            return None


        return (
            snap["filtered"]["lat"],
            snap["filtered"]["lon"],
            snap["packet_age"]
        )


phone_gps = PhoneGPS()


class _Handler(
    BaseHTTPRequestHandler
):

    server_version = (
        "FollowMeGPS/2.0"
    )


    def log_message(
        self,
        fmt,
        *args
    ):
        # Keep normal 4 Hz traffic quiet.
        return


    def _send_json(
        self,
        status,
        data
    ):

        body = json.dumps(
            data
        ).encode(
            "utf-8"
        )


        self.send_response(
            status
        )


        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )


        self.send_header(
            "Content-Length",
            str(
                len(body)
            )
        )


        self.send_header(
            "Cache-Control",
            "no-store"
        )


        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )


        self.end_headers()


        self.wfile.write(
            body
        )


    def do_GET(self):

        path = (
            self.path
            .split(
                "?",
                1
            )[0]
        )


        # -------------------------
        # Health endpoint
        # -------------------------

        if path == "/health":

            snap = (
                phone_gps.snapshot()
            )


            self._send_json(
                200,
                {
                    "ok": True,

                    "has_packet":
                        snap["packet"]
                        is not None,

                    "has_filtered_fix":
                        snap["filtered"]
                        is not None,

                    "packet_age":
                        snap[
                            "packet_age"
                        ],

                    "good_fix_age":
                        snap[
                            "good_fix_age"
                        ]
                }
            )


            return


        # -------------------------
        # iPhone HTML page
        # -------------------------

        if path not in (
            "/",
            "/phone_gps.html"
        ):

            self.send_error(
                404
            )

            return


        page = (
            Path(__file__)
            .resolve()
            .with_name(
                config.PHONE_GPS_PAGE
            )
        )


        try:

            body = (
                page.read_bytes()
            )


        except OSError as error:

            self._send_json(
                500,
                {
                    "ok": False,
                    "error":
                        "cannot read "
                        "phone page: "
                        + str(error)
                }
            )

            return


        self.send_response(
            200
        )


        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )


        self.send_header(
            "Content-Length",
            str(
                len(body)
            )
        )


        # Important for Safari:
        # always fetch newest page.
        self.send_header(
            "Cache-Control",
            "no-store, no-cache, "
            "must-revalidate"
        )


        self.end_headers()


        self.wfile.write(
            body
        )


    def do_POST(self):

        path = (
            self.path
            .split(
                "?",
                1
            )[0]
        )


        if path != "/gps":

            self._send_json(
                404,
                {
                    "ok": False,
                    "error":
                        "not found"
                }
            )

            return


        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )


            if (
                length <= 0
                or length > 16384
            ):

                raise ValueError(
                    "invalid Content-Length"
                )


            raw = (
                self.rfile.read(
                    length
                )
            )


            payload = json.loads(
                raw.decode(
                    "utf-8"
                )
            )


            accepted_new_fix = (
                phone_gps.update(
                    payload
                )
            )


            snap = (
                phone_gps.snapshot()
            )


            self._send_json(
                200,
                {
                    "ok": True,

                    "acceptedNewFix":
                        accepted_new_fix,

                    "samples":
                        (
                            0
                            if snap[
                                "filtered"
                            ]
                            is None

                            else snap[
                                "filtered"
                            ][
                                "samples"
                            ]
                        )
                }
            )


        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError
        ) as error:

            print(
                "[GPS SERVER] "
                "rejected packet:",
                error
            )


            self._send_json(
                400,
                {
                    "ok": False,
                    "error":
                        str(error)
                }
            )


    def do_OPTIONS(self):

        self.send_response(
            204
        )


        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )


        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )


        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )


        self.send_header(
            "Cache-Control",
            "no-store"
        )


        self.end_headers()


def start_server(
    certfile="cert.pem",
    keyfile="key.pem"
):

    """
    Start HTTPS GPS/page server
    in a background thread.
    """

    server = (
        ThreadingHTTPServer(
            (
                config.GPS_SERVER_HOST,
                config.GPS_SERVER_PORT
            ),
            _Handler
        )
    )


    ctx = ssl.SSLContext(
        ssl.PROTOCOL_TLS_SERVER
    )


    ctx.load_cert_chain(
        certfile=certfile,
        keyfile=keyfile
    )


    server.socket = (
        ctx.wrap_socket(
            server.socket,
            server_side=True
        )
    )


    thread = (
        threading.Thread(
            target=
                server.serve_forever,

            daemon=True
        )
    )


    thread.start()


    print(
        "[GPS SERVER] listening on "
        f"https://{config.GPS_SERVER_HOST}:"
        f"{config.GPS_SERVER_PORT}"
    )


    print(
        "[GPS SERVER] iPhone page: "
        "https://<server-address>:"
        f"{config.GPS_SERVER_PORT}/"
        "phone_gps.html"
    )


    return server
