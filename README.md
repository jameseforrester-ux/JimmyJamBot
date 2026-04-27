# ✈️ Weather Forecast Telegram Bot

A Telegram bot that delivers **high-confidence temperature forecasts** for any airport or city worldwide, using:

- **METAR / TAF** (Aviation Weather Center)
- **ECMWF IFS** (best global model)
- **GFS 0.25°** (NOAA)
- **Canadian GEM**
- **JMA** (Japan Meteorological Agency)
- **GraphCast-weighted blend** (Google AI model via Open-Meteo)
- **Polymarket** live prediction market odds

All forecasts are shown in the **station's local date and time**, not yours.

---

## Features

| Feature | Detail |
|---|---|
| Multi-model consensus | 5 models averaged + 85% confidence interval |
| ±2°F probability | % of models agreeing within 2°F of best bet |
| METAR current obs | Real-time temp, wind, visibility, raw string |
| TAF max temp | Parsed from TX group |
| Polymarket odds | Live market prices + BUY/AVOID recommendations |
| Hourly monitoring | Checks for forecast drift every 60 minutes |
| Change alerts | Instant alert if high or low shifts ≥2°F |
| Local timezone | All dates/times shown in station's local TZ |

---

## Bot Commands

```
/forecast <airport or city>   — Get full forecast immediately
/monitor <airport or city>    — Start hourly monitoring + alerts
/stopmonitor <airport or city> — Stop monitoring a station
/list                         — List all monitored stations
/help                         — Show help
```

**Examples:**
```
/forecast KDEN
/forecast KBKF
/forecast Hong Kong
/forecast London Heathrow
/monitor KATL
/monitor Miami
/stopmonitor KATL
```

---

## VPS Deployment

### Prerequisites
- Ubuntu 20.04+ VPS
- Python 3.10+
- Git

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/weather-forecast-bot.git
cd weather-forecast-bot
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Run manually (test)

```bash
python bot.py
```

Test by sending `/start` to your bot in Telegram.

### 4. Run as a systemd service (production)

```bash
# Copy files to /opt
sudo cp -r . /opt/weather-forecast-bot
sudo chown -R ubuntu:ubuntu /opt/weather-forecast-bot

# Rebuild venv in /opt
cd /opt/weather-forecast-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install and start the service
sudo cp weather-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable weather-bot
sudo systemctl start weather-bot

# Check status
sudo systemctl status weather-bot

# View logs
sudo journalctl -u weather-bot -f
```

---

## Configuration (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `BOT_TOKEN` | (set) | Your Telegram bot token |
| `CHECK_INTERVAL_SECONDS` | `3600` | How often to poll for changes (1 hour) |
| `TEMP_ALERT_THRESHOLD_F` | `2.0` | Alert if forecast shifts this many °F |
| `PROB_WINDOW_F` | `2.0` | Window for "prob within ±2°F" stat |

---

## Data Sources

### Weather Models (via Open-Meteo — free, no API key)
| Model | Provider | Resolution |
|---|---|---|
| ECMWF IFS | European Centre for Medium-Range Weather | 0.4° |
| GFS | NOAA / NWS | 0.25° |
| GEM | Environment Canada | Seamless |
| JMA | Japan Meteorological Agency | Seamless |
| GraphCast blend | Google AI + Open-Meteo best_match | — |

### METAR / TAF
- **Aviation Weather Center** (`aviationweather.gov`) — free, no key required
- Supports all ICAO station codes worldwide

### Polymarket
- **Gamma API** — public, no key required
- Searches for active temperature markets matching location + date
- Recommends BUY positions aligned with model best bet

---

## How Probability is Calculated

1. All 5 models are queried for daily max/min temperature
2. Mean and standard deviation computed across models
3. **Best bet** = ECMWF IFS value (if available) — best track record for global sites
4. **85% CI** = mean ± 1.44 × std (approximate normal distribution)
5. **Prob within ±2°F** = % of models whose forecast falls within 2°F of the mean

---

## Monitoring & Alerts

When you run `/monitor <location>`:
- The bot fetches an initial forecast snapshot (best_bet high and low for today + tomorrow)
- Every hour, it re-fetches and compares
- If the best-bet high **or** low changes by ≥ 2°F, you get an alert with the old and new value

Snapshot data is saved to `monitored_stations.json` and survives bot restarts.

---

## File Structure

```
weather-forecast-bot/
├── bot.py                 # Main bot, command handlers, scheduler
├── weather.py             # Geocoding, METAR/TAF, Open-Meteo multi-model
├── polymarket.py          # Polymarket Gamma API + position recommendations
├── formatter.py           # Telegram message formatting
├── config.py              # Bot token, thresholds, API URLs
├── requirements.txt       # Python dependencies
├── weather-bot.service    # systemd service file
└── README.md
```

---

## Troubleshooting

**Bot not responding:**
```bash
sudo journalctl -u weather-bot -n 50
```

**METAR not showing (city search):**
METAR requires an ICAO code. Use `/forecast KJFK` instead of `/forecast New York` for full METAR/TAF data.

**Polymarket shows no markets:**
Not all locations/dates have active Polymarket temperature markets. This is normal.

**Restarting after config change:**
```bash
sudo systemctl restart weather-bot
```

---

## License

MIT
