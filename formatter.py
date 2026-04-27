"""
Format forecast data into Telegram-friendly Markdown messages.
"""

from weather import f_to_c
from typing import Optional


MODEL_LABELS = {
    "ecmwf_ifs04": "ECMWF IFS",
    "gfs025": "GFS 0.25°",
    "gem_seamless": "Canadian GEM",
    "jma_seamless": "JMA",
    "best_match": "GraphCast/Best",
}


def format_consensus_block(consensus: dict, label: str) -> str:
    if not consensus:
        return f"  {label}: N/A\n"

    bb = consensus["best_bet"]
    ci_low = consensus["ci_low_85"]
    ci_high = consensus["ci_high_85"]
    prob = consensus["prob_within_2f"]
    n = consensus["n_models"]
    std = consensus["std"]

    bb_c = f_to_c(bb)
    ci_low_c = f_to_c(ci_low)
    ci_high_c = f_to_c(ci_high)

    lines = [
        f"  *{label}*",
        f"  Best Bet: *{bb:.0f}°F / {bb_c}°C*",
        f"  85% CI: {ci_low:.0f}–{ci_high:.0f}°F ({ci_low_c}–{ci_high_c}°C)",
        f"  Prob within ±2°F: *{prob}%* ({n} models)",
        f"  Model std dev: ±{std:.1f}°F",
    ]
    return "\n".join(lines)


def format_model_breakdown(model_data: dict) -> str:
    lines = ["  _Model breakdown:_"]
    for model, temps in model_data.items():
        label = MODEL_LABELS.get(model, model)
        h = temps.get("high_f")
        l = temps.get("low_f")
        if h is not None:
            lines.append(f"  • {label}: H {h:.0f}°F / L {l:.0f}°F" if l else f"  • {label}: H {h:.0f}°F")
    return "\n".join(lines)


def format_metar_block(metar: Optional[dict]) -> str:
    if not metar:
        return "  _No METAR data available_"

    temp_c = metar.get("temp_c")
    temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None
    dew_c = metar.get("dewpoint_c")
    wind_dir = metar.get("wind_dir", "")
    wind_kt = metar.get("wind_kt", "")
    vis = metar.get("visibility", "")
    altim = metar.get("altimeter", "")
    wx = metar.get("wx", "")
    obs_time = metar.get("time", "")

    lines = ["  _Current METAR:_"]
    if temp_f is not None:
        lines.append(f"  Temp: {temp_f:.0f}°F ({temp_c}°C)")
    if dew_c is not None:
        dew_f = round(dew_c * 9 / 5 + 32, 1)
        lines.append(f"  Dewpoint: {dew_f:.0f}°F ({dew_c}°C)")
    if wind_dir and wind_kt:
        lines.append(f"  Wind: {wind_dir}° @ {wind_kt} kt")
    if vis:
        lines.append(f"  Visibility: {vis} SM")
    if altim:
        lines.append(f"  Altimeter: {altim} inHg")
    if wx:
        lines.append(f"  Weather: {wx}")
    if obs_time:
        lines.append(f"  Observed: {obs_time}")
    lines.append(f"  Raw: `{metar.get('raw', 'N/A')}`")
    return "\n".join(lines)


def format_taf_block(taf_raw: Optional[str], taf_max_f: Optional[float]) -> str:
    if not taf_raw:
        return "  _No TAF available_"

    lines = ["  _TAF Summary:_"]
    if taf_max_f is not None:
        taf_max_c = f_to_c(taf_max_f)
        lines.append(f"  TAF Max Temp: {taf_max_f:.0f}°F ({taf_max_c}°C)")
    # Show first 3 lines of raw TAF
    taf_lines = [l.strip() for l in taf_raw.split("\n") if l.strip()][:4]
    lines.append(f"  `{'  '.join(taf_lines)}`")
    return "\n".join(lines)


def format_forecast_message(forecast: dict, include_polymarket: Optional[str] = None) -> str:
    """Build full Telegram message from forecast dict."""
    loc = forecast["location"]
    name = loc.get("name", "Unknown")
    country = loc.get("country", "")
    icao = loc.get("icao", "")
    local_now = forecast["local_now"]

    header_parts = [name]
    if icao:
        header_parts.append(f"({icao})")
    if country:
        header_parts.append(country)

    lines = [
        f"🌤 *Weather Forecast — {' '.join(header_parts)}*",
        f"📍 Local time: {local_now}",
        "",
    ]

    for day_key in ["today", "tomorrow"]:
        day = forecast.get(day_key)
        if not day:
            continue

        lines.append(f"━━━ *{day['display_date']}* ({day_key}) ━━━")
        lines.append("")

        # High
        high_block = format_consensus_block(day["high"], "🌡 HIGH")
        lines.append(high_block)
        lines.append("")

        # Low
        low_block = format_consensus_block(day["low"], "🌙 LOW")
        lines.append(low_block)
        lines.append("")

        # Model breakdown
        if day.get("model_breakdown"):
            lines.append(format_model_breakdown(day["model_breakdown"]))
            lines.append("")

    # METAR
    lines.append("━━━ *Current Conditions* ━━━")
    lines.append(format_metar_block(forecast.get("metar")))
    lines.append("")

    # TAF
    lines.append("━━━ *TAF* ━━━")
    lines.append(format_taf_block(forecast.get("taf_raw"), forecast.get("taf_max_f")))
    lines.append("")

    # Polymarket
    if include_polymarket:
        lines.append("━━━ *Polymarket Markets* ━━━")
        lines.append(include_polymarket)
        lines.append("")

    lines.append("_Next check in 1 hour. Alerts if high/low changes ≥2°F._")

    return "\n".join(lines)


def format_alert_message(location_name: str, icao: str, day_label: str,
                          date_str: str, field: str,
                          old_f: float, new_f: float) -> str:
    """Format a change-alert message."""
    direction = "⬆️ UP" if new_f > old_f else "⬇️ DOWN"
    delta = abs(new_f - old_f)
    field_label = "HIGH" if field == "high_f" else "LOW"
    old_c = f_to_c(old_f)
    new_c = f_to_c(new_f)

    return (
        f"🚨 *Forecast Alert — {location_name}* ({icao})\n\n"
        f"*{day_label} {field_label}* has changed {direction}\n"
        f"Was: {old_f:.0f}°F ({old_c}°C)\n"
        f"Now: *{new_f:.0f}°F ({new_c}°C)*\n"
        f"Change: {delta:.1f}°F\n\n"
        f"_Run /forecast {icao or location_name} for full update._"
    )
