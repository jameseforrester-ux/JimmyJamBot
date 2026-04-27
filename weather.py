"""
Weather data fetching:
  - Open-Meteo multi-model forecast (ECMWF, GFS, GEM, JMA, GraphCast-weighted)
  - Aviation Weather Center METAR / TAF
  - Timezone-aware local date/time for station
"""

import requests
import statistics
from datetime import datetime, timedelta
from typing import Optional
import pytz
from timezonefinder import TimezoneFinder

from config import (
    AWC_BASE, OPEN_METEO_BASE, GEOCODE_BASE,
    WEATHER_MODELS, PRIMARY_MODEL, PROB_WINDOW_F
)

tf = TimezoneFinder()

# ──────────────────────────────────────────────────────────────
# Geocoding / ICAO lookup
# ──────────────────────────────────────────────────────────────

def geocode_location(query: str) -> Optional[dict]:
    """Return {lat, lon, name, country} for a city or airport ICAO."""
    # First try AWC airport lookup
    icao = query.strip().upper()
    if 3 <= len(icao) <= 4:
        metar = fetch_metar(icao)
        if metar:
            return {
                "lat": metar["lat"],
                "lon": metar["lon"],
                "name": metar.get("name", icao),
                "icao": icao,
                "country": metar.get("country", ""),
            }

    # Fall back to Open-Meteo geocoding
    try:
        r = requests.get(
            GEOCODE_BASE,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            loc = results[0]
            return {
                "lat": loc["latitude"],
                "lon": loc["longitude"],
                "name": loc.get("name", query),
                "country": loc.get("country", ""),
                "icao": None,
            }
    except Exception as e:
        print(f"[geocode] error: {e}")
    return None


def get_timezone(lat: float, lon: float) -> str:
    """Return IANA timezone string for coordinates."""
    tz = tf.timezone_at(lat=lat, lng=lon)
    return tz or "UTC"


def local_now(lat: float, lon: float) -> datetime:
    tz = pytz.timezone(get_timezone(lat, lon))
    return datetime.now(tz)


# ──────────────────────────────────────────────────────────────
# METAR / TAF
# ──────────────────────────────────────────────────────────────

def fetch_metar(icao: str) -> Optional[dict]:
    """Fetch latest METAR for an ICAO station."""
    try:
        r = requests.get(
            f"{AWC_BASE}/metar",
            params={"ids": icao, "format": "json", "hours": 2},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        obs = data[0]
        return {
            "raw": obs.get("rawOb", "N/A"),
            "temp_c": obs.get("temp"),
            "dewpoint_c": obs.get("dewp"),
            "wind_dir": obs.get("wdir"),
            "wind_kt": obs.get("wspd"),
            "visibility": obs.get("visib"),
            "altimeter": obs.get("altim"),
            "wx": obs.get("wxString", ""),
            "clouds": obs.get("clouds", []),
            "time": obs.get("reportTime", ""),
            "lat": obs.get("lat"),
            "lon": obs.get("lon"),
            "name": obs.get("name", icao),
            "country": obs.get("country", ""),
            "icao": icao,
        }
    except Exception as e:
        print(f"[METAR] error for {icao}: {e}")
        return None


def fetch_taf(icao: str) -> Optional[str]:
    """Fetch latest TAF raw text for an ICAO station."""
    try:
        r = requests.get(
            f"{AWC_BASE}/taf",
            params={"ids": icao, "format": "json", "type": "raw"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        taf = data[0]
        raw = taf.get("rawTAF") or taf.get("tafText", "")
        return raw.strip() if raw else None
    except Exception as e:
        print(f"[TAF] error for {icao}: {e}")
        return None


def parse_taf_temp(raw_taf: str) -> Optional[float]:
    """Extract max temperature from TAF TX group (°C)."""
    if not raw_taf:
        return None
    import re
    # TX format: TX12/1518Z  (max 12°C at 15:18Z)
    matches = re.findall(r"TX(M?\d+)/\d+Z", raw_taf)
    if matches:
        temps = []
        for m in matches:
            sign = -1 if m.startswith("M") else 1
            temps.append(sign * int(m.replace("M", "")))
        return max(temps)
    return None


# ──────────────────────────────────────────────────────────────
# Open-Meteo multi-model forecast
# ──────────────────────────────────────────────────────────────

def fetch_model_forecast(lat: float, lon: float, model: str, days: int = 2) -> Optional[dict]:
    """
    Fetch daily max/min temperature from Open-Meteo for a specific model.
    Returns dict keyed by date string -> {high_f, low_f}
    """
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": max(days + 1, 3),
        }
        if model != "best_match":
            params["models"] = model

        r = requests.get(OPEN_METEO_BASE, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])

        result = {}
        for i, d in enumerate(dates):
            if i < len(highs) and highs[i] is not None:
                result[d] = {
                    "high_f": round(highs[i], 1),
                    "low_f": round(lows[i], 1) if i < len(lows) and lows[i] is not None else None,
                }
        return result
    except Exception as e:
        print(f"[OpenMeteo] model={model} error: {e}")
        return None


def fetch_all_models(lat: float, lon: float, days: int = 2) -> dict:
    """
    Fetch forecasts from all configured models.
    Returns {date_str: {model: {high_f, low_f}}}
    """
    all_data = {}
    for model in WEATHER_MODELS:
        fc = fetch_model_forecast(lat, lon, model, days)
        if fc:
            for date_str, temps in fc.items():
                if date_str not in all_data:
                    all_data[date_str] = {}
                all_data[date_str][model] = temps
    return all_data


# ──────────────────────────────────────────────────────────────
# Probability & consensus
# ──────────────────────────────────────────────────────────────

def compute_consensus(model_data: dict, field: str = "high_f") -> dict:
    """
    Given {model: {high_f, low_f}}, compute:
      - mean, median, std
      - 85% confidence range
      - probability within ±PROB_WINDOW_F of mean
    """
    values = [v[field] for v in model_data.values() if v.get(field) is not None]
    if not values:
        return {}

    mean = statistics.mean(values)
    median = statistics.median(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0

    # Models within ±window of mean
    in_window = sum(1 for v in values if abs(v - mean) <= PROB_WINDOW_F)
    prob_pct = round((in_window / len(values)) * 100)

    # 85% CI: mean ± 1.44*std (approx)
    z85 = 1.44
    ci_low = round(mean - z85 * std, 1)
    ci_high = round(mean + z85 * std, 1)

    # Best bet: weighted toward PRIMARY_MODEL if available, else median
    primary_val = model_data.get(PRIMARY_MODEL, {}).get(field)
    best_bet = primary_val if primary_val is not None else median

    return {
        "mean": round(mean, 1),
        "median": round(median, 1),
        "best_bet": round(best_bet, 1),
        "std": round(std, 1),
        "ci_low_85": ci_low,
        "ci_high_85": ci_high,
        "prob_within_2f": prob_pct,
        "n_models": len(values),
        "all_values": sorted(values),
    }


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


# ──────────────────────────────────────────────────────────────
# Full forecast for a location
# ──────────────────────────────────────────────────────────────

def get_full_forecast(location: dict) -> Optional[dict]:
    """
    Given a location dict {lat, lon, name, icao, ...}, return full forecast:
    - Today and tomorrow in local time
    - Multi-model consensus
    - METAR current conditions
    - TAF implied high
    """
    lat = location["lat"]
    lon = location["lon"]
    icao = location.get("icao")

    loc_now = local_now(lat, lon)
    today_str = loc_now.strftime("%Y-%m-%d")
    tomorrow = loc_now + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    # Multi-model forecasts
    model_fc = fetch_all_models(lat, lon, days=2)

    days_out = {}
    for date_str, label in [(today_str, "today"), (tomorrow_str, "tomorrow")]:
        date_models = model_fc.get(date_str, {})
        high_consensus = compute_consensus(date_models, "high_f")
        low_consensus = compute_consensus(date_models, "low_f")

        days_out[label] = {
            "date_str": date_str,
            "local_label": f"{label.capitalize()} ({loc_now.strftime('%Z')})",
            "display_date": datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d"),
            "high": high_consensus,
            "low": low_consensus,
            "model_breakdown": date_models,
        }

    # METAR current
    metar = fetch_metar(icao) if icao else None

    # TAF
    taf_raw = fetch_taf(icao) if icao else None
    taf_max_c = parse_taf_temp(taf_raw) if taf_raw else None
    taf_max_f = round(taf_max_c * 9 / 5 + 32, 1) if taf_max_c is not None else None

    return {
        "location": location,
        "local_now": loc_now.strftime("%Y-%m-%d %H:%M %Z"),
        "today": days_out.get("today"),
        "tomorrow": days_out.get("tomorrow"),
        "metar": metar,
        "taf_raw": taf_raw,
        "taf_max_f": taf_max_f,
    }
