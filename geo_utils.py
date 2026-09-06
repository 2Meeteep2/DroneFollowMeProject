# geo_utils.py
"""Small geo-math helpers: distance between two GPS points, and computing
a point that is a given distance/bearing away from another point.
Good enough accuracy for offsets of a few tens of meters."""

import math

EARTH_RADIUS_M = 6371000.0


def distance_m(lat1, lon1, lat2, lon2):
    """Haversine distance between two lat/lon points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def offset_point(lat, lon, bearing_deg, distance_m_):
    """Returns (lat, lon) that is distance_m_ meters away from (lat, lon)
    in the direction bearing_deg (0 = north, 90 = east, 180 = south)."""
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_m_ / EARTH_RADIUS_M

    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), math.degrees(lambda2)
