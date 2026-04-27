"""
Polymarket Gamma API integration.
Searches for temperature markets matching a location and date,
returns current odds and recommended positions.
"""

import requests
from datetime import datetime
from typing import Optional
from config import POLYMARKET_GAMMA


def search_temp_markets(location_name: str, date_str: str) -> list[dict]:
    """
    Search Polymarket for highest-temperature markets matching location + date.
    Returns list of market dicts with outcome probabilities.
    """
    # Build search queries
    city = location_name.split(",")[0].strip()
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%B %-d")       # e.g. "April 25"
    date_display2 = date_obj.strftime("%-m/%-d")     # e.g. "4/25"

    queries = [
        f"highest temperature {city} {date_display}",
        f"temperature {city} {date_display2}",
        f"temperature {city}",
    ]

    markets = []
    seen_ids = set()

    for q in queries:
        try:
            r = requests.get(
                f"{POLYMARKET_GAMMA}/markets",
                params={
                    "q": q,
                    "limit": 10,
                    "active": True,
                    "closed": False,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()

            items = data if isinstance(data, list) else data.get("markets", [])
            for m in items:
                mid = m.get("id") or m.get("conditionId", "")
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                question = m.get("question", "").lower()
                # Filter to temperature-relevant markets
                if any(kw in question for kw in ["temperature", "temp", "high", "degree"]):
                    if city.lower() in question or date_display.lower() in question or date_display2 in question:
                        markets.append(m)
        except Exception as e:
            print(f"[Polymarket] search '{q}' error: {e}")
            continue

        if markets:
            break

    return markets


def parse_market_outcomes(market: dict) -> list[dict]:
    """
    Parse a Polymarket market into outcome list:
    [{label, price, implied_prob_pct}]
    """
    outcomes = []
    tokens = market.get("tokens") or market.get("outcomes", [])

    for tok in tokens:
        outcome_val = tok.get("outcome") or tok.get("name", "")
        price = tok.get("price")
        if price is None:
            # Try outcomePrices
            pass
        if price is not None:
            outcomes.append({
                "label": outcome_val,
                "price": round(float(price), 3),
                "implied_prob_pct": round(float(price) * 100, 1),
            })

    # Sort by prob descending
    outcomes.sort(key=lambda x: x["implied_prob_pct"], reverse=True)
    return outcomes


def get_polymarket_recommendation(
    location_name: str,
    date_str: str,
    best_bet_f: float,
    ci_low_f: float,
    ci_high_f: float,
) -> Optional[str]:
    """
    Main function: search markets, parse outcomes, recommend positions.
    Returns formatted string or None if no markets found.
    """
    markets = search_temp_markets(location_name, date_str)
    if not markets:
        return None

    lines = []
    for market in markets[:3]:  # Max 3 markets
        question = market.get("question", "Unknown market")
        volume = market.get("volume") or market.get("volumeNum", 0)
        url = f"https://polymarket.com/event/{market.get('slug', '')}"

        outcomes = parse_market_outcomes(market)
        if not outcomes:
            continue

        lines.append(f"\n📊 *{question}*")
        if volume:
            try:
                lines.append(f"   Volume: ${float(volume):,.0f}")
            except Exception:
                pass
        lines.append(f"   🔗 {url}\n")

        # Show all outcomes
        lines.append("   Outcomes:")
        for o in outcomes:
            lines.append(f"   • {o['label']}: {o['implied_prob_pct']}% (${o['price']:.3f})")

        # Recommend positions based on best_bet
        lines.append("\n   🎯 *Recommended positions:*")
        recs = []
        for o in outcomes:
            label = o["label"].strip()
            # Try to parse numeric temperature from outcome label
            try:
                import re
                nums = re.findall(r"-?\d+\.?\d*", label)
                if nums:
                    outcome_temp = float(nums[0])
                    # If this outcome temp is within our CI and near best bet
                    if ci_low_f <= outcome_temp <= ci_high_f:
                        edge = best_bet_f - outcome_temp
                        if abs(edge) <= 2:
                            recs.append(f"   ✅ BUY '{label}' — aligns with {best_bet_f:.0f}°F best bet (prob: {o['implied_prob_pct']}%)")
                        elif outcome_temp < ci_low_f - 3 or outcome_temp > ci_high_f + 3:
                            recs.append(f"   ❌ AVOID '{label}' — outside model CI")
                    else:
                        if outcome_temp < ci_low_f - 2 or outcome_temp > ci_high_f + 2:
                            recs.append(f"   ❌ AVOID '{label}' — outside 85% CI range")
            except Exception:
                continue

        if recs:
            lines.extend(recs)
        else:
            # Generic guidance
            top = outcomes[0]
            lines.append(f"   ✅ Market leader: '{top['label']}' at {top['implied_prob_pct']}%")
            lines.append(f"   Compare to model best bet: {best_bet_f:.0f}°F")

    return "\n".join(lines) if lines else None
