"""
Configuration for Weather Forecast Telegram Bot
"""

# Telegram Bot Token
BOT_TOKEN = "8598130653:AAGiup-jVp4F8GHFieDJDuh5cd9ucadz5V8"

# How often to check for forecast changes (seconds)
CHECK_INTERVAL_SECONDS = 3600  # 1 hour

# Alert threshold — send alert if high OR low changes by this many °F
TEMP_ALERT_THRESHOLD_F = 2.0

# Probability window (±degrees F) for confidence reporting
PROB_WINDOW_F = 2.0

# Data file for persistent monitored stations
DATA_FILE = "monitored_stations.json"

# Aviation Weather Center API base
AWC_BASE = "https://aviationweather.gov/api/data"

# Open-Meteo base
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo geocoding
GEOCODE_BASE = "https://geocoding-api.open-meteo.com/v1/search"

# Polymarket Gamma API
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"

# Weather models to query via Open-Meteo
# Includes ECMWF IFS, GFS, Google GraphCast (via best_match), UK Met, Canadian GEM
WEATHER_MODELS = [
    "ecmwf_ifs04",      # ECMWF IFS 0.4° (best global model)
    "gfs025",           # NOAA GFS 0.25°
    "gem_seamless",     # Canadian GEM
    "jma_seamless",     # Japan Meteorological Agency
    "best_match",       # Open-Meteo best match (includes GraphCast weighting)
]

# Primary model for "best bet" single answer
PRIMARY_MODEL = "ecmwf_ifs04"
