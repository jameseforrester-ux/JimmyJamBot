"""
Weather Forecast Telegram Bot
Monitors airport/city weather using METAR/TAF + multi-model forecasts.
Sends hourly checks and alerts when forecast changes ≥ 2°F.
"""

import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    BOT_TOKEN, DATA_FILE, CHECK_INTERVAL_SECONDS, TEMP_ALERT_THRESHOLD_F
)
from weather import geocode_location, get_full_forecast, local_now
from polymarket import get_polymarket_recommendation
from formatter import format_forecast_message, format_alert_message

# ──────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Persistent storage
# ──────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Load monitored stations and cached forecasts from disk."""
    p = Path(DATA_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"monitors": {}}   # {chat_id: {station_key: {location, last_forecast}}}


def save_data(data: dict):
    Path(DATA_FILE).write_text(json.dumps(data, indent=2, default=str))


# Shared state (in-memory + persisted)
BOT_DATA = load_data()


def get_monitors(chat_id: str) -> dict:
    return BOT_DATA.setdefault("monitors", {}).setdefault(str(chat_id), {})


def set_monitor(chat_id: str, key: str, value: dict):
    BOT_DATA.setdefault("monitors", {}).setdefault(str(chat_id), {})[key] = value
    save_data(BOT_DATA)


def remove_monitor(chat_id: str, key: str):
    monitors = BOT_DATA.get("monitors", {}).get(str(chat_id), {})
    monitors.pop(key, None)
    save_data(BOT_DATA)


# ──────────────────────────────────────────────────────────────
# Helper: extract best_bet snapshot for change detection
# ──────────────────────────────────────────────────────────────

def snapshot_forecast(forecast: dict) -> dict:
    """Extract just the best_bet values for change detection."""
    snap = {}
    for day_key in ["today", "tomorrow"]:
        day = forecast.get(day_key)
        if day:
            snap[day_key] = {
                "date_str": day["date_str"],
                "high_f": day["high"].get("best_bet"),
                "low_f": day["low"].get("best_bet"),
            }
    return snap


def detect_changes(old_snap: dict, new_snap: dict) -> list[dict]:
    """Return list of significant changes."""
    changes = []
    for day_key in ["today", "tomorrow"]:
        old_day = old_snap.get(day_key, {})
        new_day = new_snap.get(day_key, {})
        date_str = new_day.get("date_str", "")

        for field in ["high_f", "low_f"]:
            old_val = old_day.get(field)
            new_val = new_day.get(field)
            if old_val is None or new_val is None:
                continue
            if abs(new_val - old_val) >= TEMP_ALERT_THRESHOLD_F:
                changes.append({
                    "day_key": day_key,
                    "date_str": date_str,
                    "field": field,
                    "old_f": old_val,
                    "new_f": new_val,
                })
    return changes


# ──────────────────────────────────────────────────────────────
# Command Handlers
# ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ *Weather Forecast Bot*\n\n"
        "I give high-confidence temperature forecasts using ECMWF IFS, GFS, GEM, JMA, "
        "GraphCast-weighted models + METAR/TAF data, with Polymarket market data.\n\n"
        "*Commands:*\n"
        "`/forecast <airport or city>` — Get forecast now\n"
        "`/monitor <airport or city>` — Start monitoring (hourly alerts)\n"
        "`/stopmonitor <airport or city>` — Stop monitoring\n"
        "`/list` — Show monitored stations\n"
        "`/help` — Show this message\n\n"
        "Examples:\n"
        "`/forecast KDEN`\n"
        "`/forecast Chicago`\n"
        "`/monitor KBKF`\n"
        "`/monitor Hong Kong`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_forecast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Fetch and display forecast for a location."""
    chat_id = update.effective_chat.id
    query = " ".join(ctx.args).strip() if ctx.args else ""

    if not query:
        await update.message.reply_text(
            "Please provide a location.\nExample: `/forecast KDEN` or `/forecast London`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.message.reply_text(f"🔍 Fetching forecast for *{query}*...", parse_mode=ParseMode.MARKDOWN)

    # Geocode
    location = geocode_location(query)
    if not location:
        await msg.edit_text(f"❌ Could not find location: *{query}*\nTry an ICAO code (e.g. KORD) or city name.", parse_mode=ParseMode.MARKDOWN)
        return

    # Fetch forecast
    forecast = get_full_forecast(location)
    if not forecast:
        await msg.edit_text("❌ Failed to fetch forecast data. Please try again.", parse_mode=ParseMode.MARKDOWN)
        return

    # Polymarket for today
    today_data = forecast.get("today", {})
    poly_text = None
    if today_data:
        high_bet = today_data.get("high", {}).get("best_bet")
        ci_low = today_data.get("high", {}).get("ci_low_85")
        ci_high = today_data.get("high", {}).get("ci_high_85")
        if high_bet:
            poly_text = get_polymarket_recommendation(
                location["name"],
                today_data["date_str"],
                high_bet,
                ci_low or high_bet - 4,
                ci_high or high_bet + 4,
            )

    text = format_forecast_message(forecast, include_polymarket=poly_text)

    # Telegram has 4096 char limit per message — split if needed
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        await msg.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN)
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
    else:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Add a location to monitoring list."""
    chat_id = update.effective_chat.id
    query = " ".join(ctx.args).strip() if ctx.args else ""

    if not query:
        await update.message.reply_text(
            "Please provide a location.\nExample: `/monitor KDEN`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.message.reply_text(f"🔍 Setting up monitoring for *{query}*...", parse_mode=ParseMode.MARKDOWN)

    location = geocode_location(query)
    if not location:
        await msg.edit_text(f"❌ Could not find location: *{query}*", parse_mode=ParseMode.MARKDOWN)
        return

    # Use ICAO or city name as key
    station_key = location.get("icao") or location["name"].lower().replace(" ", "_")

    # Get initial forecast snapshot
    forecast = get_full_forecast(location)
    snap = snapshot_forecast(forecast) if forecast else {}

    set_monitor(str(chat_id), station_key, {
        "location": location,
        "query": query,
        "last_snapshot": snap,
        "added_at": datetime.utcnow().isoformat(),
    })

    name = location["name"]
    icao = location.get("icao", "")
    label = f"{name} ({icao})" if icao else name

    await msg.edit_text(
        f"✅ Now monitoring *{label}*\n\n"
        f"I'll check every hour and alert you if the forecast high or low changes by ≥{TEMP_ALERT_THRESHOLD_F:.0f}°F.\n\n"
        f"Run `/forecast {query}` to see the current forecast.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stopmonitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove a location from monitoring."""
    chat_id = update.effective_chat.id
    query = " ".join(ctx.args).strip() if ctx.args else ""

    if not query:
        await update.message.reply_text("Please provide a location. Example: `/stopmonitor KDEN`", parse_mode=ParseMode.MARKDOWN)
        return

    monitors = get_monitors(str(chat_id))

    # Try to match by key, ICAO, or name
    matched_key = None
    for key, val in monitors.items():
        loc = val.get("location", {})
        if (
            key.upper() == query.upper()
            or loc.get("icao", "").upper() == query.upper()
            or loc.get("name", "").lower() == query.lower()
            or val.get("query", "").lower() == query.lower()
        ):
            matched_key = key
            break

    if not matched_key:
        await update.message.reply_text(
            f"❌ No active monitor found for *{query}*.\nUse `/list` to see active monitors.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    loc = monitors[matched_key].get("location", {})
    name = loc.get("name", matched_key)
    remove_monitor(str(chat_id), matched_key)
    await update.message.reply_text(f"🛑 Stopped monitoring *{name}*.", parse_mode=ParseMode.MARKDOWN)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """List all monitored stations for this chat."""
    chat_id = update.effective_chat.id
    monitors = get_monitors(str(chat_id))

    if not monitors:
        await update.message.reply_text(
            "No stations monitored yet. Use `/monitor <location>` to start.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["📡 *Monitored Stations:*\n"]
    for key, val in monitors.items():
        loc = val.get("location", {})
        name = loc.get("name", key)
        icao = loc.get("icao", "")
        added = val.get("added_at", "")[:10]
        label = f"{name} ({icao})" if icao else name
        lines.append(f"• *{label}* — added {added}")
        lines.append(f"  Stop: `/stopmonitor {val.get('query', key)}`")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ──────────────────────────────────────────────────────────────
# Hourly scheduler job
# ──────────────────────────────────────────────────────────────

async def hourly_check(application: Application):
    """Run every hour: check all monitored stations, send alerts if changed."""
    logger.info("Running hourly forecast check...")
    data = load_data()
    monitors_all = data.get("monitors", {})
    changed = False

    for chat_id_str, stations in monitors_all.items():
        chat_id = int(chat_id_str)

        for station_key, monitor in list(stations.items()):
            location = monitor.get("location")
            if not location:
                continue

            try:
                forecast = get_full_forecast(location)
                if not forecast:
                    continue

                new_snap = snapshot_forecast(forecast)
                old_snap = monitor.get("last_snapshot", {})
                changes = detect_changes(old_snap, new_snap)

                name = location.get("name", station_key)
                icao = location.get("icao", "")

                for change in changes:
                    alert_text = format_alert_message(
                        location_name=name,
                        icao=icao,
                        day_label=change["day_key"].capitalize(),
                        date_str=change["date_str"],
                        field=change["field"],
                        old_f=change["old_f"],
                        new_f=change["new_f"],
                    )
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=alert_text,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        logger.info(f"Alert sent to {chat_id} for {station_key}: {change}")
                    except Exception as e:
                        logger.error(f"Failed to send alert to {chat_id}: {e}")

                # Update snapshot
                monitor["last_snapshot"] = new_snap
                changed = True

            except Exception as e:
                logger.error(f"Error checking {station_key}: {e}")

    if changed:
        save_data(BOT_DATA)
        # Sync in-memory
        global BOT_DATA
        BOT_DATA = load_data()


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Register commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("forecast", cmd_forecast))
    application.add_handler(CommandHandler("monitor", cmd_monitor))
    application.add_handler(CommandHandler("stopmonitor", cmd_stopmonitor))
    application.add_handler(CommandHandler("list", cmd_list))

    # Set bot command menu
    async def post_init(app: Application):
        await app.bot.set_my_commands([
            BotCommand("forecast", "Get forecast for a location"),
            BotCommand("monitor", "Monitor a location for changes"),
            BotCommand("stopmonitor", "Stop monitoring a location"),
            BotCommand("list", "List monitored stations"),
            BotCommand("help", "Show help message"),
        ])

        # Start scheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            hourly_check,
            "interval",
            seconds=CHECK_INTERVAL_SECONDS,
            args=[app],
            id="hourly_check",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"Scheduler started — checking every {CHECK_INTERVAL_SECONDS}s")

    application.post_init = post_init

    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
