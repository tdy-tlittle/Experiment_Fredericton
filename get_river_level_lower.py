"""Print the current Saint John River water level at Fredericton, NB.

Data source: Government of Canada open hydrometric API
https://api.weather.gc.ca/collections/hydrometric-realtime
"""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

STATION_NUMBER = "01AK003"  # SAINT JOHN RIVER AT FREDERICTON
API_URL = (
    "https://api.weather.gc.ca/collections/hydrometric-realtime/items"
    f"?STATION_NUMBER={STATION_NUMBER}&sortby=-DATETIME&f=json&limit=1"
)


def fetch_latest_level(url: str) -> dict:
    """Fetch the latest hydrometric record for the configured station."""
    with urlopen(url, timeout=20) as response:
        payload = json.load(response)

    features = payload.get("features", [])
    if not features:
        raise ValueError("No data returned for the station.")

    properties = features[0].get("properties", {})
    level = properties.get("LEVEL")
    if level is None:
        raise ValueError("Latest record does not include a water level.")

    return {
        "station_name": properties.get("STATION_NAME", "Unknown station"),
        "station_number": properties.get("STATION_NUMBER", STATION_NUMBER),
        "level_m": level,
        "timestamp_utc": properties.get("DATETIME", "Unknown time"),
        "timestamp_local": properties.get("DATETIME_LST", "Unknown local time"),
    }


def main() -> int:
    try:
        data = fetch_latest_level(API_URL)
    except HTTPError as exc:
        print(f"HTTP error while fetching water level: {exc.code} {exc.reason}")
        return 1
    except URLError as exc:
        print(f"Network error while fetching water level: {exc.reason}")
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Data error while reading water level: {exc}")
        return 1

    print(
        f"Current lower Saint John River water level in Fredericton, NB "
        f"({data['station_name']} - {data['station_number']}):"
    )
    print(f"  Level: {data['level_m']:.3f} m")
    print(f"  Timestamp (local): {data['timestamp_local']}")
    print(f"  Timestamp (UTC):   {data['timestamp_utc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
