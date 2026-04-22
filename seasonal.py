import os, requests, logging, sqlite3, json
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from db import get_connection

log = logging.getLogger(__name__)

CALENDARIFIC_API_KEY = os.getenv("CALENDARIFIC_API_KEY", "")

MULTIPLIER_MAP = {
    "diwali": 1.40,
    "eid": 1.25,
    "christmas": 1.25,
    "navratri": 1.20,
    "holi": 1.10,
    "independence": 1.10,
    "republic": 1.10,
    "earth day": 1.0,  # Neutral/Sustainability holiday
    "default": 1.05
}

def fetch_from_calendarific(year: int) -> List[Dict[str, Any]]:
    """Fetch holidays from Calendarific for India."""
    # Handle missing or placeholder key
    if not CALENDARIFIC_API_KEY or "your_" in CALENDARIFIC_API_KEY:
        log.warning("Valid CALENDARIFIC_API_KEY not found. Using hardcoded core festivals for %d.", year)
        # Fallback for 2026 major festivals
        if year == 2026:
            return [
                {"name": "Republic Day", "date": {"iso": "2026-01-26"}, "type": ["national"]},
                {"name": "Holi", "date": {"iso": "2026-03-04"}, "type": ["religious"]},
                {"name": "Eid al-Fitr", "date": {"iso": "2026-03-20"}, "type": ["religious"]},
                {"name": "Ambedkar Jayanti", "date": {"iso": "2026-04-14"}, "type": ["national"]},
                {"name": "Earth Day", "date": {"iso": "2026-04-22"}, "type": ["observance"], "category_hint": "eco-friendly"},
                {"name": "Labor Day / May Day", "date": {"iso": "2026-05-01"}, "type": ["national"]},
                {"name": "Independence Day", "date": {"iso": "2026-08-15"}, "type": ["national"]},
                {"name": "Navratri", "date": {"iso": "2026-10-10"}, "type": ["religious"]},
                {"name": "Diwali / Deepavali", "date": {"iso": "2026-11-08"}, "type": ["religious"]},
                {"name": "Christmas", "date": {"iso": "2026-12-25"}, "type": ["observance"]},
            ]
        return []

    url = "https://calendarific.com/api/v2/holidays"
    params = {
        "api_key": CALENDARIFIC_API_KEY,
        "country": "IN",
        "year": year,
        "type": "national,religious,observance"
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        holidays = data.get("response", {}).get("holidays", [])
        return holidays
    except Exception as exc:
        log.error("Failed to fetch from Calendarific: %s", exc)
        return []

def _get_multiplier(name: str) -> float:
    name_lower = name.lower()
    for key, val in MULTIPLIER_MAP.items():
        if key in name_lower:
            return val
    return MULTIPLIER_MAP["default"]

def get_festivals_cached(year: int) -> List[Dict[str, Any]]:
    """Get festivals from cache or fetch and store if missing."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM festival_cache WHERE year = ?", (year,))
        rows = cur.fetchall()
        
        if rows:
            return [dict(row) for row in rows]
        
        # Cache miss, fetch from API
        log.info("Festival cache miss for %d. Fetching from API...", year)
        holidays = fetch_from_calendarific(year)
        if not holidays:
            return []

        processed = []
        now_iso = datetime.now().isoformat()
        
        with conn:
            for h in holidays:
                name = h.get("name", "Unknown Festival")
                h_date = h.get("date", {}).get("iso", "")[:10] # YYYY-MM-DD
                if not h_date: continue
                
                # Extension logic: start-2, end+2
                dt = datetime.strptime(h_date, "%Y-%m-%d")
                start_date = (dt - timedelta(days=2)).strftime("%Y-%m-%d")
                end_date = (dt + timedelta(days=2)).strftime("%Y-%m-%d")
                
                multiplier = _get_multiplier(name)
                
                # Basic category hint based on metadata
                cat_hint = "general"
                if h.get("type"):
                    cat_hint = h["type"][0]

                conn.execute(
                    """INSERT INTO festival_cache 
                       (year, name, start_date, end_date, multiplier, category_hint, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (year, name, start_date, end_date, multiplier, cat_hint, now_iso)
                )
                processed.append({
                    "name": name,
                    "start_date": start_date,
                    "end_date": end_date,
                    "multiplier": multiplier
                })
        return processed
    finally:
        conn.close()

def get_active_events(target_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Return festivals active on the target date."""
    if target_date is None:
        target_date = date.today()
    
    festivals = get_festivals_cached(target_date.year)
    active = []
    target_str = target_date.strftime("%Y-%m-%d")
    
    for f in festivals:
        if f["start_date"] <= target_str <= f["end_date"]:
            active.append(f)
    return active

def get_seasonal_context(target_date: Optional[date] = None) -> Dict[str, Any]:
    """Return a detailed seasonal context dictionary."""
    if target_date is None:
        target_date = date.today()
    
    active = get_active_events(target_date)
    
    # Upcoming logic (next 7 days)
    upcoming = []
    week_later = target_date + timedelta(days=7)
    target_str = target_date.strftime("%Y-%m-%d")
    week_later_str = week_later.strftime("%Y-%m-%d")
    
    all_festivals = get_festivals_cached(target_date.year)
    for f in all_festivals:
        # Check if start is between now and week_later, but not active now
        if target_str < f["start_date"] <= week_later_str:
            upcoming.append(f)
    
    peak_multiplier = max([f["multiplier"] for f in active] or [1.0])
    is_peak = len(active) > 0
    
    summary_parts = []
    if active:
        names = [f["name"] for f in active]
        summary_parts.append(f"Active festivals: {', '.join(names)}")
    if upcoming:
        names = [f["name"] for f in upcoming]
        summary_parts.append(f"Upcoming festivals: {', '.join(names)}")
    
    context_str = ". ".join(summary_parts) if summary_parts else "No active or upcoming seasonal events."
    
    return {
        "active_events": active,
        "upcoming_events": upcoming,
        "peak_multiplier": peak_multiplier,
        "is_peak": is_peak,
        "context_str": context_str
    }

def clear_festival_cache(year: int):
    """Clear festival cache for a specific year to force refresh."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM festival_cache WHERE year = ?", (year,))
        log.info("Cleared festival cache for year %d", year)
    finally:
        conn.close()
