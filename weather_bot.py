"""
EDGE Weather Intelligence Bot
──────────────────────────────
Queries ECMWF + 4 other open-source AI weather models via Open-Meteo.
Gives temperature range predictions and monitors hourly for changes.
Sends Polymarket position-change alerts when forecasts shift.
"""

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8706407555:AAHAFWSx5TM4GNK_1srIK6Km-rr2xGmTGHQ")

# Model weights — ECMWF gets highest bias
MODELS = {
    "ecmwf":         ("ecmwf_ifs025",          0.40),
    "gfs":           ("gfs_seamless",           0.20),
    "icon":          ("icon_seamless",           0.20),
    "meteo_france":  ("meteo_france_seamless",   0.12),
    "gem":           ("gem_seamless",            0.08),
}

# Alert threshold: how many degrees change triggers a Polymarket alert
ALERT_THRESHOLD_F = 2.0   # Fahrenheit
ALERT_THRESHOLD_C = 1.1   # Celsius

# How often to check (seconds) — 3600 = 1 hour
MONITOR_INTERVAL = 3600

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── OPEN-METEO API ──────────────────────────────────────────────────────────

async def geocode(query: str) -> Optional[dict]:
    """
    Convert city name, airport name, or IATA/ICAO code to lat/lon + timezone.
    Tries IATA lookup first, then Open-Meteo geocoding.
    """
    # Known military / small airport overrides
    AIRPORT_OVERRIDES = {
        "BUCKLEY": {"name": "Buckley Space Force Base", "lat": 39.7016, "lon": -104.7516, "tz": "America/Denver"},
        "KBKF":    {"name": "Buckley Space Force Base", "lat": 39.7016, "lon": -104.7516, "tz": "America/Denver"},
        "KDEN":    {"name": "Denver International Airport", "lat": 39.8561, "lon": -104.6737, "tz": "America/Denver"},
        "KJFK":    {"name": "JFK Airport New York", "lat": 40.6413, "lon": -73.7781, "tz": "America/New_York"},
        "KLAX":    {"name": "LAX Los Angeles", "lat": 33.9425, "lon": -118.4081, "tz": "America/Los_Angeles"},
        "EGLL":    {"name": "London Heathrow", "lat": 51.4775, "lon": -0.4614, "tz": "Europe/London"},
        "LFPG":    {"name": "Paris Charles de Gaulle", "lat": 49.0097, "lon": 2.5479, "tz": "Europe/Paris"},
        "RJTT":    {"name": "Tokyo Haneda", "lat": 35.5494, "lon": 139.7798, "tz": "Asia/Tokyo"},
        "OMDB":    {"name": "Dubai International", "lat": 25.2532, "lon": 55.3657, "tz": "Asia/Dubai"},
    }
    q_upper = query.upper().strip()
    for key, val in AIRPORT_OVERRIDES.items():
        if key in q_upper:
            return val

    # Open-Meteo geocoding
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": 1, "language": "en", "format": "json"},
            )
            data = r.json()
            if data.get("results"):
                res = data["results"][0]
                return {
                    "name": f"{res.get('name', query)}, {res.get('country', '')}",
                    "lat": res["latitude"],
                    "lon": res["longitude"],
                    "tz": res.get("timezone", "UTC"),
                }
    except Exception as e:
        logger.error(f"Geocode error: {e}")
    return None


async def fetch_model_forecast(lat: float, lon: float, tz: str, model_api: str) -> Optional[dict]:
    """Fetch hourly + daily temperature data for one model."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": tz,
                    "models": model_api,
                    "hourly": "temperature_2m,apparent_temperature",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "forecast_days": 3,
                },
            )
            return r.json()
    except Exception as e:
        logger.error(f"Open-Meteo error ({model_api}): {e}")
        return None


async def fetch_all_models(lat: float, lon: float, tz: str) -> dict:
    """Fetch all models concurrently and return parsed results."""
    tasks = {
        name: fetch_model_forecast(lat, lon, tz, api)
        for name, (api, _) in MODELS.items()
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {name: res for name, res in zip(tasks.keys(), results) if isinstance(res, dict)}


def blend_forecasts(model_data: dict) -> dict:
    """
    Weighted blend of all models. ECMWF gets 40% weight.
    Returns blended daily high/low for day 0, 1, 2.
    """
    blended = {0: {"max": [], "min": []}, 1: {"max": [], "min": []}, 2: {"max": [], "min": []}}
    model_details = {}

    for name, data in model_data.items():
        weight = MODELS[name][1]
        daily = data.get("daily", {})
        maxes = daily.get("temperature_2m_max", [])
        mins  = daily.get("temperature_2m_min", [])
        model_details[name] = {"max": maxes[:3], "min": mins[:3], "weight": weight}
        for i in range(min(3, len(maxes))):
            blended[i]["max"].append((maxes[i], weight))
            blended[i]["min"].append((mins[i], weight))

    result = {}
    for day_idx in range(3):
        max_vals = blended[day_idx]["max"]
        min_vals = blended[day_idx]["min"]

        if not max_vals:
            continue

        total_w = sum(w for _, w in max_vals)
        blended_max = sum(v * w for v, w in max_vals) / total_w
        blended_min = sum(v * w for v, w in min_vals) / total_w

        # Tighten range for day 0 (today), wider for days 1-2
        margin = 1.0 if day_idx == 0 else 3.0
        result[day_idx] = {
            "high":      round(blended_max, 1),
            "low":       round(blended_min, 1),
            "high_range": (round(blended_max - margin, 1), round(blended_max + margin, 1)),
            "low_range":  (round(blended_min - margin, 1), round(blended_min + margin, 1)),
            "margin":    margin,
        }

    return result, model_details


def probability_analysis(model_details: dict, day_idx: int) -> dict:
    """
    Cross-model agreement analysis.
    Returns probability that high/low will be within range.
    """
    maxes = [(d["max"][day_idx], d["weight"]) for d in model_details.values() if len(d["max"]) > day_idx]
    mins  = [(d["min"][day_idx],  d["weight"]) for d in model_details.values() if len(d["min"]) > day_idx]

    if not maxes:
        return {}

    avg_max = sum(v * w for v, w in maxes) / sum(w for _, w in maxes)
    avg_min = sum(v * w for v, w in mins)  / sum(w for _, w in mins)

    # Std deviation across models
    std_max = math.sqrt(sum(w * (v - avg_max)**2 for v, w in maxes) / sum(w for _, w in maxes))
    std_min = math.sqrt(sum(w * (v - avg_min)**2 for v, w in mins)  / sum(w for _, w in mins))

    margin = 1.0 if day_idx == 0 else 3.0

    # Probability within ±margin based on model spread
    # Lower std = higher confidence
    prob_high = max(50, min(99, int(100 - (std_max / margin) * 30)))
    prob_low  = max(50, min(99, int(100 - (std_min / margin) * 30)))

    # Model agreement count
    agree_count = sum(
        1 for v, _ in maxes
        if abs(v - avg_max) <= margin
    )
    agreement_pct = int((agree_count / len(maxes)) * 100) if maxes else 0

    return {
        "prob_high": prob_high,
        "prob_low":  prob_low,
        "std_max":   round(std_max, 1),
        "std_min":   round(std_min, 1),
        "agreement": agreement_pct,
        "n_models":  len(maxes),
    }


def build_forecast_message(location: dict, blended: dict, model_details: dict, dates: list) -> str:
    """Build the full formatted Telegram message."""
    day_labels = ["📅 TODAY", "📅 TOMORROW", "📅 DAY AFTER"]
    margin_note = ["±1°F (high confidence)", "±3°F (planning range)", "±3°F (planning range)"]

    lines = [
        f"🌡 *EDGE Weather Intelligence*",
        f"📍 *{location['name']}*",
        f"🕐 {datetime.now().strftime('%H:%M')} local · ECMWF-weighted ensemble\n",
    ]

    for i in range(3):
        if i not in blended:
            continue
        b = blended[i]
        prob = probability_analysis(model_details, i)
        date_str = dates[i].strftime("%a %b %d") if i < len(dates) else ""

        lines.append(f"{'─'*28}")
        lines.append(f"*{day_labels[i]}* — {date_str}")
        lines.append(f"🔴 High: *{b['high_range'][0]}–{b['high_range'][1]}°F* ({b['high']}°F center)")
        lines.append(f"🔵 Low:  *{b['low_range'][0]}–{b['low_range'][1]}°F* ({b['low']}°F center)")
        lines.append(f"📐 Range: {margin_note[i]}")
        if prob:
            lines.append(f"🎯 High confidence: *{prob['prob_high']}%* | Low: *{prob['prob_low']}%*")
            lines.append(f"🤝 Model agreement: {prob['agreement']}% ({prob['n_models']} models)")
        lines.append("")

    # Per-model breakdown
    lines.append(f"{'─'*28}")
    lines.append("*📊 Model Breakdown (Today's High)*")
    model_emojis = {
        "ecmwf": "🇪🇺 ECMWF",
        "gfs": "🇺🇸 GFS",
        "icon": "🇩🇪 ICON",
        "meteo_france": "🇫🇷 Météo-France",
        "gem": "🇨🇦 GEM",
    }
    for name, d in model_details.items():
        if d["max"]:
            label = model_emojis.get(name, name.upper())
            weight_pct = int(MODELS[name][1] * 100)
            lines.append(f"{label} ({weight_pct}% weight): *{d['max'][0]}°F* high / *{d['min'][0]}°F* low")

    lines.append(f"\n⚡ _Data: Open-Meteo · ECMWF IFS025 · GFS · ICON · MF · GEM_")
    return "\n".join(lines)


def build_alert_message(location: dict, old_blended: dict, new_blended: dict, day_idx: int, date_str: str) -> str:
    """Build the Polymarket change alert."""
    day_labels = {0: "TODAY", 1: "TOMORROW", 2: "DAY AFTER"}
    ob = old_blended[day_idx]
    nb = new_blended[day_idx]
    delta_high = round(nb["high"] - ob["high"], 1)
    delta_low  = round(nb["low"]  - ob["low"],  1)
    direction_high = "🔺" if delta_high > 0 else "🔻"
    direction_low  = "🔺" if delta_low  > 0 else "🔻"

    return (
        f"⚠️ *EDGE FORECAST CHANGE ALERT*\n"
        f"📍 *{location['name']}*\n"
        f"📅 *{day_labels.get(day_idx, '')}* — {date_str}\n\n"
        f"*HIGH TEMP CHANGED:*\n"
        f"  Was: {ob['high_range'][0]}–{ob['high_range'][1]}°F\n"
        f"  Now: {nb['high_range'][0]}–{nb['high_range'][1]}°F\n"
        f"  {direction_high} Shift: {delta_high:+.1f}°F\n\n"
        f"*LOW TEMP CHANGED:*\n"
        f"  Was: {ob['low_range'][0]}–{ob['low_range'][1]}°F\n"
        f"  Now: {nb['low_range'][0]}–{nb['low_range'][1]}°F\n"
        f"  {direction_low} Shift: {delta_low:+.1f}°F\n\n"
        f"💡 *ACTION REQUIRED:* Review your Polymarket temperature positions for {location['name']}.\n"
        f"🕐 Detected at {datetime.now().strftime('%H:%M')} — next check in 1 hour."
    )


# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🌡 *EDGE Weather Intelligence Bot*\n\n"
        "Powered by ECMWF + GFS + ICON + Météo-France + GEM\n\n"
        "*Commands:*\n"
        "/weather `<location>` — Get 3-day temperature forecast\n"
        "/monitor `<location>` — Start hourly monitoring + Polymarket alerts\n"
        "/watching — Show all monitored locations\n"
        "/stop `<location>` — Stop monitoring a location\n"
        "/stopall — Stop all monitoring\n"
        "/help — Show this message\n\n"
        "*Examples:*\n"
        "`/weather Buckley Air Force Base`\n"
        "`/weather KDEN`\n"
        "`/weather Tokyo`\n"
        "`/monitor KBKF`\n\n"
        "⚡ Current day: ±1°F range | Next 2 days: ±3°F range\n"
        "🎯 ECMWF weighted at 40% · alerts fire on ≥2°F shift"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_weather(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Usage: `/weather <city or airport>`\nExample: `/weather Denver`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text(f"🔍 Fetching forecasts for *{query}*…", parse_mode=ParseMode.MARKDOWN)

    location = await geocode(query)
    if not location:
        await msg.edit_text(f"❌ Could not find location: `{query}`\nTry a city name or ICAO code (e.g. KDEN, EGLL)", parse_mode=ParseMode.MARKDOWN)
        return

    model_data = await fetch_all_models(location["lat"], location["lon"], location["tz"])
    if not model_data:
        await msg.edit_text("❌ Failed to fetch weather data. Try again in a moment.")
        return

    blended, model_details = blend_forecasts(model_data)

    # Build date list in location timezone
    today = datetime.now()
    dates = [today + timedelta(days=i) for i in range(3)]

    text = build_forecast_message(location, blended, model_details, dates)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Monitor this location hourly", callback_data=f"monitor:{query}")],
        [InlineKeyboardButton("🔄 Refresh now", callback_data=f"refresh:{query}")],
    ])

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    # Store forecast in context for monitoring comparison
    ctx.bot_data.setdefault("forecasts", {})[query.upper()] = {
        "location": location,
        "blended": blended,
        "model_details": model_details,
        "fetched_at": datetime.now().isoformat(),
    }


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Usage: `/monitor <city or airport>`", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    await start_monitoring(update, ctx, query, chat_id)


async def start_monitoring(update_or_query, ctx, query, chat_id):
    """Fetch initial forecast, store it, schedule hourly job."""
    is_callback = not hasattr(update_or_query, 'message')
    send = update_or_query.edit_message_text if is_callback else update_or_query.message.reply_text

    await send(f"📡 Setting up monitoring for *{query}*…", parse_mode=ParseMode.MARKDOWN)

    location = await geocode(query)
    if not location:
        await send(f"❌ Could not find: `{query}`", parse_mode=ParseMode.MARKDOWN)
        return

    model_data = await fetch_all_models(location["lat"], location["lon"], location["tz"])
    if not model_data:
        await send("❌ Failed to fetch initial forecast.", parse_mode=ParseMode.MARKDOWN)
        return

    blended, model_details = blend_forecasts(model_data)
    today = datetime.now()
    dates = [today + timedelta(days=i) for i in range(3)]

    # Store baseline
    job_key = f"{chat_id}:{query.upper()}"
    ctx.bot_data.setdefault("monitoring", {})[job_key] = {
        "query": query,
        "location": location,
        "blended": blended,
        "model_details": model_details,
        "dates": [d.isoformat() for d in dates],
        "chat_id": chat_id,
        "started": datetime.now().isoformat(),
    }

    # Cancel existing job if any
    existing = ctx.bot_data.get("jobs", {}).get(job_key)
    if existing:
        try:
            existing.schedule_removal()
        except Exception:
            pass

    # Schedule hourly check
    job = ctx.job_queue.run_repeating(
        monitor_job,
        interval=MONITOR_INTERVAL,
        first=MONITOR_INTERVAL,
        data={"job_key": job_key},
        name=job_key,
        chat_id=chat_id,
    )
    ctx.bot_data.setdefault("jobs", {})[job_key] = job

    forecast_text = build_forecast_message(location, blended, model_details, dates)
    monitoring_notice = (
        f"\n\n{'─'*28}\n"
        f"📡 *MONITORING ACTIVE*\n"
        f"Checking every hour. You'll receive an alert if the forecast shifts ≥{ALERT_THRESHOLD_F}°F.\n"
        f"Type /stop `{query}` to cancel."
    )

    await send(forecast_text + monitoring_notice, parse_mode=ParseMode.MARKDOWN)


async def monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Hourly job: re-fetch forecast, compare to baseline, alert on change."""
    job_key = ctx.job.data["job_key"]
    stored  = ctx.bot_data.get("monitoring", {}).get(job_key)
    if not stored:
        ctx.job.schedule_removal()
        return

    chat_id  = stored["chat_id"]
    location = stored["location"]
    query    = stored["query"]

    try:
        model_data = await fetch_all_models(location["lat"], location["lon"], location["tz"])
        if not model_data:
            return

        new_blended, new_model_details = blend_forecasts(model_data)
        old_blended = stored["blended"]
        dates = [datetime.fromisoformat(d) for d in stored["dates"]]

        alerts_sent = False

        for day_idx in range(3):
            if day_idx not in old_blended or day_idx not in new_blended:
                continue

            old_high = old_blended[day_idx]["high"]
            new_high = new_blended[day_idx]["high"]
            old_low  = old_blended[day_idx]["low"]
            new_low  = new_blended[day_idx]["low"]

            delta_high = abs(new_high - old_high)
            delta_low  = abs(new_low  - old_low)

            if delta_high >= ALERT_THRESHOLD_F or delta_low >= ALERT_THRESHOLD_F:
                date_str = dates[day_idx].strftime("%a %b %d") if day_idx < len(dates) else ""
                alert_text = build_alert_message(location, old_blended, new_blended, day_idx, date_str)
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=alert_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                alerts_sent = True

        # Update stored baseline
        ctx.bot_data["monitoring"][job_key]["blended"] = new_blended
        ctx.bot_data["monitoring"][job_key]["model_details"] = new_model_details

        if not alerts_sent:
            logger.info(f"[{job_key}] Hourly check: no significant change.")

    except Exception as e:
        logger.error(f"Monitor job error ({job_key}): {e}")


async def cmd_watching(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    monitoring = ctx.bot_data.get("monitoring", {})
    active = {k: v for k, v in monitoring.items() if v["chat_id"] == chat_id}

    if not active:
        await update.message.reply_text("📡 No locations currently being monitored.\nUse `/monitor <location>` to start.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["📡 *Currently Monitoring:*\n"]
    for job_key, data in active.items():
        started = datetime.fromisoformat(data["started"]).strftime("%b %d %H:%M")
        lines.append(f"• *{data['location']['name']}*\n  Started: {started}\n  Stop: `/stop {data['query']}`")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = " ".join(ctx.args)
    chat_id = update.effective_chat.id

    if not query:
        await update.message.reply_text("Usage: `/stop <location>`", parse_mode=ParseMode.MARKDOWN)
        return

    job_key = f"{chat_id}:{query.upper()}"
    monitoring = ctx.bot_data.get("monitoring", {})

    if job_key not in monitoring:
        # Try partial match
        matches = [k for k in monitoring if query.upper() in k and monitoring[k]["chat_id"] == chat_id]
        if not matches:
            await update.message.reply_text(f"❌ No active monitor for `{query}`.\nUse /watching to see active monitors.", parse_mode=ParseMode.MARKDOWN)
            return
        job_key = matches[0]

    # Cancel job
    job = ctx.bot_data.get("jobs", {}).get(job_key)
    if job:
        try:
            job.schedule_removal()
        except Exception:
            pass
        ctx.bot_data["jobs"].pop(job_key, None)

    loc_name = monitoring[job_key]["location"]["name"]
    ctx.bot_data["monitoring"].pop(job_key, None)
    await update.message.reply_text(f"✅ Stopped monitoring *{loc_name}*.", parse_mode=ParseMode.MARKDOWN)


async def cmd_stopall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    monitoring = ctx.bot_data.get("monitoring", {})
    to_stop = [k for k, v in monitoring.items() if v["chat_id"] == chat_id]

    for job_key in to_stop:
        job = ctx.bot_data.get("jobs", {}).get(job_key)
        if job:
            try: job.schedule_removal()
            except Exception: pass
        ctx.bot_data.get("jobs", {}).pop(job_key, None)
        monitoring.pop(job_key, None)

    if to_stop:
        await update.message.reply_text(f"✅ Stopped {len(to_stop)} monitor(s).", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("No active monitors to stop.", parse_mode=ParseMode.MARKDOWN)


async def btn_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    data   = query.data
    chat_id = update.effective_chat.id

    if data.startswith("monitor:"):
        location_query = data.split(":", 1)[1]
        await start_monitoring(query, ctx, location_query, chat_id)

    elif data.startswith("refresh:"):
        location_query = data.split(":", 1)[1]
        location = await geocode(location_query)
        if not location:
            await query.edit_message_text(f"❌ Could not refresh: `{location_query}`", parse_mode=ParseMode.MARKDOWN)
            return
        model_data = await fetch_all_models(location["lat"], location["lon"], location["tz"])
        blended, model_details = blend_forecasts(model_data)
        today = datetime.now()
        dates = [today + timedelta(days=i) for i in range(3)]
        text = build_forecast_message(location, blended, model_details, dates)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📡 Monitor this location hourly", callback_data=f"monitor:{location_query}")],
            [InlineKeyboardButton("🔄 Refresh now", callback_data=f"refresh:{location_query}")],
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def fallback_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle plain text as weather query."""
    text = update.message.text.strip()
    # If it looks like a location query, treat as /weather
    if len(text) > 2 and not text.startswith('/'):
        ctx.args = text.split()
        await cmd_weather(update, ctx)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Set your BOT_TOKEN in the script or as an environment variable:")
        print("   export BOT_TOKEN=your_token_here")
        print("   python weather_bot.py")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_start))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("watching",cmd_watching))
    app.add_handler(CommandHandler("stop",    cmd_stop))
    app.add_handler(CommandHandler("stopall", cmd_stopall))
    app.add_handler(CallbackQueryHandler(btn_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))

    print("🌡 EDGE Weather Bot running…")
    print("   Commands: /weather, /monitor, /watching, /stop, /stopall")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
