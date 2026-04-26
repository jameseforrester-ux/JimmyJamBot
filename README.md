# EDGE Weather Intelligence Bot — Setup Guide

Predicts temperature outcomes using ECMWF (40% weight) blended with GFS, ICON,
Météo-France, and GEM. Monitors hourly and sends Polymarket alerts on ≥2°F shifts.

---

## ① Get a Bot Token (2 minutes)

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Pick a name (e.g. "EDGE Weather") and username (e.g. `edge_weather_xyzbot`)
4. BotFather gives you a token like: `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
5. Copy it — you'll need it below

---

## ② Install Python (if not installed)

**Windows:** Download from https://python.org/downloads — tick "Add to PATH"
**Mac:**     `brew install python3`  or download from python.org
**Linux:**   `sudo apt install python3 python3-pip`

---

## ③ Install Dependencies

Open Terminal / PowerShell **in the folder containing this README**, then run:

```
pip install -r requirements.txt
```

---

## ④ Add Your Token

Open `weather_bot.py` in any text editor (Notepad, VSCode, etc.)

Find this line near the top:
```python
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
```

Replace `PASTE_YOUR_BOT_TOKEN_HERE` with your actual token:
```python
BOT_TOKEN = os.getenv("BOT_TOKEN", "7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
```

Save the file.

---

## ⑤ Run the Bot

```
python weather_bot.py
```

You should see:
```
🌡 EDGE Weather Bot running…
```

Keep this Terminal window open while using the bot. It runs locally on your machine.

---

## ⑥ Use the Bot

Open Telegram → find your bot by its username → send:

| Command | What it does |
|---------|-------------|
| `/weather Denver` | 3-day forecast with probability % |
| `/weather Buckley Air Force Base` | Same for specific station |
| `/weather KDEN` | Works with ICAO codes |
| `/weather KBKF` | Buckley Space Force Base (built-in) |
| `/monitor KBKF` | Hourly check, alerts on ≥2°F shift |
| `/watching` | List all active monitors |
| `/stop Denver` | Stop monitoring Denver |
| `/stopall` | Stop everything |

You can also just **type any location name** without a command and it will look it up.

---

## How the Forecast Works

**Today (Day 0):** ±1°F range — high confidence, tight bound
**Tomorrow + Day After:** ±3°F range — planning range

**Model Weights:**
- 🇪🇺 ECMWF IFS025 — **40%** (primary, best global accuracy)
- 🇺🇸 GFS — 20%
- 🇩🇪 ICON — 20%
- 🇫🇷 Météo-France — 12%
- 🇨🇦 GEM — 8%

**Probability scores** are calculated from cross-model standard deviation.
Low spread = high agreement = high probability shown.

---

## Polymarket Alert Logic

When monitoring is active, every hour the bot:
1. Re-fetches all 5 models
2. Computes new blended forecast
3. Compares to the baseline (first fetch)
4. If high OR low shifts ≥2°F → sends alert to your chat

The alert tells you exactly what changed and which day, so you know
whether to adjust your temperature position on Polymarket.

---

## Running 24/7 (optional)

To keep the bot running when you close your laptop:

**Free option — always-on PC:**
Just leave the terminal open.

**VPS option (cheapest):**
- Get a $4/mo VPS from Hetzner or DigitalOcean
- Upload `weather_bot.py` and `requirements.txt`
- Run: `nohup python weather_bot.py &`

**Windows background service:**
Use Task Scheduler to run `python weather_bot.py` at startup.

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'telegram'"**
→ Run: `pip install -r requirements.txt`

**"Conflict: terminated by other getUpdates request"**
→ You have two copies of the bot running. Close one.

**Bot not responding:**
→ Check the terminal for errors. Make sure token is correct.

**Location not found:**
→ Try the ICAO code (e.g. KBKF instead of "Buckley")
→ Or try the nearest city name

---

*Data provided by Open-Meteo (free, no API key needed)*
*ECMWF data via open-meteo.com — CC BY 4.0*
