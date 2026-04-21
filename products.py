import threading, time, json, sqlite3
from typing import List, Dict, Any, Optional

# Shared lock for synchronization if needed, though SQLite handles its own
_lock = threading.Lock()
# We'll use the DB_PATH from db.py
from db import DB_PATH, get_connection, get_price_history

PRODUCTS = [
    {
        "id": "P001",
        "name": "Sony WH-1000XM5 Headphones",
        "current_price": 24990,
        "cost_price": 16000,
        "category": "electronics",
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 15, "max_change_pct": 20, "positioning": "premium"},
    },
    {
        "id": "P002",
        "name": "Samsung Galaxy S24 Ultra",
        "current_price": 129999,
        "cost_price": 85000,
        "category": "smartphones",
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 12, "max_change_pct": 15, "positioning": "premium"},
    },
    {
        "id": "P003",
        "name": "Apple MacBook Air M3",
        "current_price": 114900,
        "cost_price": 78000,
        "category": "laptops",
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 10, "max_change_pct": 18, "positioning": "premium"},
    },
    {
        "id": "P004",
        "name": "Nike Air Max 270",
        "current_price": 12995,
        "cost_price": 6500,
        "category": "footwear",
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 25, "max_change_pct": 25, "positioning": "mid-range"},
    },
    {
        "id": "P005",
        "name": "Dyson V15 Detect Vacuum",
        "current_price": 62900,
        "cost_price": 38000,
        "category": "home-appliances",
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 18, "max_change_pct": 20, "positioning": "premium"},
    },
]


def _ensure_seed_products():
    """Ensure hardcoded seed products exist in the database."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("SELECT COUNT(*) FROM tracked_products")
            if cur.fetchone()[0] == 0:
                for p in PRODUCTS:
                    conn.execute(
                        """INSERT INTO tracked_products (id, name, current_price, cost_price, category, status, constraints_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (p["id"], p["name"], p["current_price"], p["cost_price"], p["category"], p["status"], 
                         json.dumps(p["constraints"]), time.strftime("%Y-%m-%dT%H:%M:%SZ"))
                    )
    finally:
        conn.close()

def get_products() -> List[Dict[str, Any]]:
    _ensure_seed_products()
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("SELECT * FROM tracked_products")
            rows = cur.fetchall()
        res = []
        for row in rows:
            p = dict(row)
            p["constraints"] = json.loads(p["constraints_json"])
            # Mix in latest price from history
            try:
                hist = get_price_history(p["id"])
                if hist:
                    p["current_price"] = hist[0]["our_price"]
                    p["last_updated"] = hist[0]["timestamp"].replace("T", " ")[:19]
            except Exception:
                pass
            res.append(p)
        return res
    finally:
        conn.close()

def get_product(pid: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM tracked_products WHERE id = ?", (pid,))
        row = cur.fetchone()
        if not row:
            return None
        p = dict(row)
        p["constraints"] = json.loads(p["constraints_json"])
        try:
            hist = get_price_history(p["id"])
            if hist:
                p["current_price"] = hist[0]["our_price"]
                p["last_updated"] = hist[0]["timestamp"].replace("T", " ")[:19]
        except Exception:
            pass
        return p
    finally:
        conn.close()

def update_product(pid: str, **kwargs) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        # Get existing
        p = get_product(pid)
        if not p:
            return None
        
        # Build update query
        fields = []
        params = []
        for k, v in kwargs.items():
            if k == "constraints":
                fields.append("constraints_json = ?")
                params.append(json.dumps(v))
            elif k in ["name", "current_price", "cost_price", "category", "status"]:
                fields.append(f"{k} = ?")
                params.append(v)
        
        if fields:
            params.append(pid)
            with conn:
                conn.execute(f"UPDATE tracked_products SET {', '.join(fields)} WHERE id = ?", params)
            
        return get_product(pid)
    finally:
        conn.close()

def set_status(pid: str, status: str) -> bool:
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("UPDATE tracked_products SET status = ? WHERE id = ?", (status, pid))
        return cur.rowcount > 0
    finally:
        conn.close()

def add_product(product_data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    try:
        # Check if already exists
        with conn:
            cur = conn.execute("SELECT id FROM tracked_products WHERE id = ?", (product_data["id"],))
            if cur.fetchone():
                return product_data
            
            conn.execute(
                """INSERT INTO tracked_products (id, name, current_price, cost_price, category, status, constraints_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    product_data["id"], product_data["name"], product_data["current_price"], 
                    product_data["cost_price"], product_data["category"], product_data["status"],
                    json.dumps(product_data.get("constraints", {})), time.strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            )
        return product_data
    finally:
        conn.close()

def remove_product(pid: str) -> bool:
    """Remove a product from the tracker and clean up its history."""
    conn = get_connection()
    try:
        with conn:
            # Delete from tracker
            cur = conn.execute("DELETE FROM tracked_products WHERE id = ?", (pid,))
            deleted_count = cur.rowcount
            
            # Clean up associated tables
            conn.execute("DELETE FROM price_history WHERE product_id = ?", (pid,))
            conn.execute("DELETE FROM scheduler_log WHERE product_id = ?", (pid,))
            
        return deleted_count > 0
    finally:
        conn.close()
