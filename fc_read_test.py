from collections import Counter
import time

from pymavlink import mavutil


PORT = "COM5"
BAUD = 115200
REPORT_INTERVAL_S = 5.0


# ========================================
# TELEMETRIE, DIE WIR ANFORDERN
# ========================================

REQUESTED_MESSAGES = {
    "SYS_STATUS": (1, 1),            # 1 Hz
    "GPS_RAW_INT": (24, 2),          # 2 Hz
    "ATTITUDE": (30, 5),             # 5 Hz
    "GLOBAL_POSITION_INT": (33, 5),  # 5 Hz
    "VFR_HUD": (74, 2),              # 2 Hz
    "BATTERY_STATUS": (147, 1),      # 1 Hz
}


def request_message_interval(
    master,
    message_id,
    rate_hz
):
    """
    Ask the flight controller to send one MAVLink
    message type at the requested rate.

    This does NOT arm the vehicle and does NOT
    change flight mode or position.
    """

    interval_us = int(
        1_000_000 / rate_hz
    )

    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
        0,
        message_id,
        interval_us,
        0,
        0,
        0,
        0,
        0,
    )


def print_message(msg):
    msg_type = msg.get_type()

    if msg_type == "HEARTBEAT":

        armed = bool(
            msg.base_mode
            & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )

        mode = mavutil.mode_string_v10(
            msg
        )

        print(
            f"[HEARTBEAT] "
            f"mode={mode} "
            f"armed={armed}"
        )


    elif msg_type == "GPS_RAW_INT":

        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt_m = msg.alt / 1000.0

        print(
            f"[GPS_RAW] "
            f"fix={msg.fix_type} "
            f"sats={msg.satellites_visible} "
            f"lat={lat:.7f} "
            f"lon={lon:.7f} "
            f"alt={alt_m:.1f}m"
        )


    elif msg_type == "GLOBAL_POSITION_INT":

        lat = msg.lat / 1e7
        lon = msg.lon / 1e7

        alt_m = (
            msg.alt / 1000.0
        )

        rel_alt_m = (
            msg.relative_alt / 1000.0
        )

        print(
            f"[POSITION] "
            f"lat={lat:.7f} "
            f"lon={lon:.7f} "
            f"alt={alt_m:.1f}m "
            f"rel_alt={rel_alt_m:.1f}m"
        )


    elif msg_type == "SYS_STATUS":

        voltage_v = (
            None
            if msg.voltage_battery == 65535
            else msg.voltage_battery / 1000.0
        )

        voltage_text = (
            "unknown"
            if voltage_v is None
            else f"{voltage_v:.2f}V"
        )

        print(
            f"[SYS_STATUS] "
            f"battery={voltage_text}"
        )


    elif msg_type == "BATTERY_STATUS":

        voltage_v = None

        if msg.voltages:

            first_cell_mv = (
                msg.voltages[0]
            )

            if (
                first_cell_mv != 65535
            ):
                voltage_v = (
                    first_cell_mv / 1000.0
                )


        voltage_text = (
            "unknown"
            if voltage_v is None
            else f"{voltage_v:.2f}V"
        )


        print(
            f"[BATTERY_STATUS] "
            f"voltage={voltage_text} "
            f"remaining="
            f"{msg.battery_remaining}%"
        )


    elif msg_type == "ATTITUDE":

        print(
            f"[ATTITUDE] "
            f"roll={msg.roll:.3f} "
            f"pitch={msg.pitch:.3f} "
            f"yaw={msg.yaw:.3f}"
        )


    elif msg_type == "VFR_HUD":

        print(
            f"[VFR_HUD] "
            f"groundspeed="
            f"{msg.groundspeed:.2f}m/s "
            f"heading="
            f"{msg.heading}deg "
            f"alt={msg.alt:.1f}m"
        )


    elif msg_type == "COMMAND_ACK":

        command_info = (
            mavutil.mavlink
            .enums["MAV_CMD"]
            .get(msg.command)
        )

        if command_info is None:
            command_text = str(
                msg.command
            )
        else:
            command_text = (
                command_info.name
            )


        result_info = (
            mavutil.mavlink
            .enums["MAV_RESULT"]
            .get(msg.result)
        )

        if result_info is None:
            result_text = str(
                msg.result
            )
        else:
            result_text = (
                result_info.name
            )


        print(
            f"[ACK] "
            f"command="
            f"{command_text} "
            f"result="
            f"{result_text}"
        )


print(
    f"Öffne {PORT} "
    f"mit {BAUD} Baud ..."
)


master = (
    mavutil.mavlink_connection(
        PORT,
        baud=BAUD,
    )
)


print(
    "Warte auf MAVLink-Heartbeat ..."
)


heartbeat = (
    master.wait_heartbeat(
        timeout=10
    )
)


if heartbeat is None:

    print(
        "Kein Heartbeat empfangen."
    )

    raise SystemExit(1)


print(
    "Heartbeat empfangen."
)

print(
    f"System-ID: "
    f"{master.target_system}"
)

print(
    f"Component-ID: "
    f"{master.target_component}"
)

print()


# ========================================
# TELEMETRIE ANFORDERN
# ========================================

print(
    "Fordere Telemetrie-Streams an ..."
)

print(
    "Es werden KEINE ARM-, MODE- "
    "oder Bewegungsbefehle gesendet."
)


for (
    message_name,
    (
        message_id,
        rate_hz
    )
) in REQUESTED_MESSAGES.items():

    request_message_interval(
        master,
        message_id,
        rate_hz,
    )

    print(
        f"  {message_name:<22} "
        f"{rate_hz} Hz"
    )

    # Kleine Pause zwischen
    # den Anfragen.
    time.sleep(0.1)


print()

print(
    "Telemetrie-Lesetest läuft."
)

print(
    "Mit Strg+C beenden."
)

print()


counts = Counter()

last_report = (
    time.monotonic()
)


try:

    while True:

        msg = master.recv_match(
            blocking=True,
            timeout=1,
        )


        if msg is not None:

            msg_type = (
                msg.get_type()
            )


            if (
                msg_type
                != "BAD_DATA"
            ):

                counts[
                    msg_type
                ] += 1


                print_message(
                    msg
                )


        now = (
            time.monotonic()
        )


        if (
            now
            - last_report
            >= REPORT_INTERVAL_S
        ):

            last_report = now


            print()

            print(
                "=== Empfangene "
                "MAVLink-Nachrichtentypen ==="
            )


            if not counts:

                print(
                    "Noch keine "
                    "Nachrichten empfangen."
                )

            else:

                for (
                    msg_type,
                    count
                ) in counts.most_common():

                    print(
                        f"{msg_type:<28} "
                        f"{count}"
                    )


            print(
                "=========================================="
            )

            print()


except KeyboardInterrupt:

    print()

    print(
        "Lesetest beendet."
    )

    print()

    print(
        "=== Endstand ==="
    )


    if not counts:

        print(
            "Keine Nachrichten empfangen."
        )

    else:

        for (
            msg_type,
            count
        ) in counts.most_common():

            print(
                f"{msg_type:<28} "
                f"{count}"
            )