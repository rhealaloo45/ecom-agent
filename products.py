"""Product data store with seed products."""
import threading, time

_lock = threading.Lock()

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


def get_products():
    with _lock:
        return [p.copy() for p in PRODUCTS]


def get_product(pid):
    with _lock:
        for p in PRODUCTS:
            if p["id"] == pid:
                return p.copy()
    return None


def update_product(pid, **kwargs):
    with _lock:
        for p in PRODUCTS:
            if p["id"] == pid:
                p.update(kwargs)
                if "current_price" in kwargs:
                    p["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    p["status"] = "Updated"
                return p.copy()
    return None


def set_status(pid, status):
    with _lock:
        for p in PRODUCTS:
            if p["id"] == pid:
                p["status"] = status
                return True
    return False
