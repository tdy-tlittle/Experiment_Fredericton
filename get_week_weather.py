import json
import urllib.request
from datetime import datetime

# Fredericton, NB coordinates
LATITUDE = 45.9636
LONGITUDE = -66.6431


def fetch_weather():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
        f"&temperature_unit=celsius"
        f"&precipitation_unit=mm"
        f"&timezone=America%2FHalifax"
        f"&forecast_days=7"
    )

    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())

    daily = data["daily"]
    dates = daily["time"]
    temp_max = daily["temperature_2m_max"]
    temp_min = daily["temperature_2m_min"]
    precip = daily["precipitation_sum"]

    print(f"7-Day Weather Forecast for Fredericton, NB")
    print("=" * 45)

    for i in range(7):
        date = datetime.strptime(dates[i], "%Y-%m-%d")
        day_name = date.strftime("%A")
        print(f"{day_name} ({dates[i]})")
        print(f"  High: {temp_max[i]:.1f}°C  |  Low: {temp_min[i]:.1f}°C")
        print(f"  Precipitation: {precip[i]:.1f} mm")
        print()


if __name__ == "__main__":
    fetch_weather()
