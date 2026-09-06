# config.py
"""Central settings for the follow-me drone system."""

# --- MAVLink / Flight Controller connection ---
TEST_MODE = True
# Real hardware (SpeedyBee F405 V5 via UART4 -> Pi GPIO14/15):
MAVLINK_CONNECTION = "/dev/serial0"
MAVLINK_BAUD = 115200


# --- Phone GPS receiver (iPhone Safari -> Pi, over WiFi) ---
GPS_SERVER_HOST = "0.0.0.0"
GPS_SERVER_PORT = 5000

# The Pi serves phone_gps.html itself over the same HTTPS origin.
PHONE_GPS_PAGE = "phone_gps.html"


# --- Bluetooth control link (Laptop -> Pi, Start/Stop) ---
BLE_LOCAL_NAME = "FollowMeDrone"

BLE_SERVICE_UUID = "12345678-1234-5678-1234-56789abc0000"

BLE_CTRL_CHAR_UUID = "12345678-1234-5678-1234-56789abc0001"

BLE_STATUS_CHAR_UUID = "12345678-1234-5678-1234-56789abc0002"


# --- Follow-me behaviour ---
FOLLOW_ALTITUDE_M = 15.0

FOLLOW_OFFSET_M = 5.0

FOLLOW_BEARING_DEG = 180

# 4 Hz control loop
UPDATE_RATE_HZ = 4.0

# Kept for backwards compatibility with the old follow_me.py.
DEADBAND_M = 0.8


# --- GPS filtering ---
PHONE_GPS_FILTER_WINDOW_SIZE = 5

# Up to 8 m accuracy: normal weighting
PHONE_GPS_GOOD_ACCURACY_M = 8.0

# 8–15 m: still accepted, but weakly weighted
# Above 15 m: ignored for position
PHONE_GPS_MAX_ACCEPTABLE_ACCURACY_M = 15.0


# --- Movement detection ---
STATIONARY_SPEED_MPS = 0.3

MOVING_SPEED_MPS = 0.7

STATIONARY_DEADBAND_M = 0.8

MOVING_DEADBAND_M = 0.6

STATIONARY_CONFIRMATIONS = 2

MOVING_CONFIRMATIONS = 1

IMMEDIATE_MOVE_M = 2.0


# --- GPS startup stabilisation ---
STARTUP_MIN_WAIT_S = 0.0

STARTUP_TIMEOUT_S = 50.0

STARTUP_REQUIRED_SAMPLES = 5

# We raised this because your iPhone often settles around 5–6 m.
STARTUP_MAX_ACCURACY_M = 6.5

STARTUP_MAX_SPREAD_M = 1.2

STARTUP_STABLE_HOLD_S = 3.0


# --- Safety ---
PHONE_LINK_TIMEOUT_S = 5.0

# Separate timeout for the last genuinely usable GPS fix.
PHONE_GPS_FIX_TIMEOUT_S = 15.0

MAVLINK_LINK_TIMEOUT_S = 3.0

MAX_RADIUS_FROM_HOME_M = 100.0

FAILSAFE_ACTION = "RTL"