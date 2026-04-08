import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "pricesync.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                our_price REAL NOT NULL,
                competitor_avg_price REAL,
                competitor_min_price REAL,
                demand_score REAL,
                trend TEXT,
                guardrail_passed INTEGER,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )
    conn.close()


def insert_price_snapshot(
    product_id: str,
    product_name: str,
    our_price: float,
    competitor_avg_price: Optional[float],
    competitor_min_price: Optional[float],
    demand_score: Optional[float],
    trend: Optional[str],
    guardrail_passed: bool,
    timestamp: Optional[str] = None,
) -> None:
    timestamp = timestamp or _iso_now()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO price_history (
                product_id, product_name, our_price,
                competitor_avg_price, competitor_min_price,
                demand_score, trend, guardrail_passed, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                product_name,
                our_price,
                competitor_avg_price,
                competitor_min_price,
                demand_score,
                trend,
                1 if guardrail_passed else 0,
                timestamp,
            ),
        )
    conn.close()


def get_price_history(product_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT product_id, product_name, our_price,
               competitor_avg_price, competitor_min_price,
               demand_score, trend, guardrail_passed, timestamp
        FROM price_history
        WHERE product_id = ?
        ORDER BY timestamp DESC
        LIMIT 30
        """,
        (product_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def log_scheduler_run(
    product_id: str,
    run_type: str,
    status: str,
    message: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> None:
    timestamp = timestamp or _iso_now()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO scheduler_log (
                product_id, run_type, status, message, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (product_id, run_type, status, message, timestamp),
        )
    conn.close()


def get_last_scheduler_runs(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT product_id, run_type, status, message, timestamp
        FROM scheduler_log
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows
