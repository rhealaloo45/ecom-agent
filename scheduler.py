import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

import db
from products import get_products
from agent import run_agent
from seasonal import clear_festival_cache, get_festivals_cached

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def _run_product(product):
    try:
        state = run_agent(product, run_type="auto")
        status = "success" if not state.get("error") else "error"
        message = state.get("error") or "Auto-monitor run completed"
        db.log_scheduler_run(product["id"], "auto", status, message)
    except Exception as exc:
        log.error("Scheduler run failed for %s: %s", product["id"], exc)
        db.log_scheduler_run(product["id"], "auto", "error", str(exc))


def _monitor_all_products():
    log.info("Scheduler starting auto-monitor run for all products")
    products = get_products()
    for product in products:
        _run_product(product)
    log.info("Scheduler completed auto-monitor run")


def _refresh_festival_cache():
    """Daily job to refresh festival cache for current and next year."""
    now = datetime.now()
    years = [now.year, now.year + 1]
    log.info("Starting daily festival cache refresh for years %s", years)
    for year in years:
        clear_festival_cache(year)
        get_festivals_cached(year)
    log.info("Festival cache refresh complete")


def start():
    if scheduler.get_jobs():
        return
    scheduler.add_job(
        _monitor_all_products,
        "interval",
        hours=2,
        id="pricesync_monitor",
        replace_existing=True,
    )
    scheduler.add_job(
        _refresh_festival_cache,
        "cron",
        hour=0,
        minute=1,
        id="festival_cache_refresh",
        replace_existing=True,
    )
    scheduler.start()
    
    # NEW: Automatically shut down the background scheduler when Flask exits
    import atexit
    atexit.register(lambda: scheduler.shutdown(wait=False))
    
    log.info("Scheduler started")


def trigger_now():
    """Immediately executes the background scheduler loop for all products."""
    job = scheduler.get_job("pricesync_monitor")
    if job:
        log.info("Force triggering background scheduler logic immediately.")
        job.modify(next_run_time=datetime.now(timezone.utc))


def get_scheduler_status():
    next_run_time = None
    jobs = scheduler.get_jobs()
    if jobs:
        next_time = jobs[0].next_run_time
        if next_time:
            next_run_time = next_time.astimezone(timezone.utc).isoformat()

    last_runs = {}
    for row in db.get_last_scheduler_runs(limit=100):
        pid = row["product_id"]
        if pid not in last_runs:
            last_runs[pid] = row["timestamp"]

    return {
        "next_run_time": next_run_time,
        "last_run_times": last_runs,
    }
