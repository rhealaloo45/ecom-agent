"""E-commerce platform connectors for product import.

Supports Shopify and WooCommerce out of the box. Products are normalized
into a unified schema and stored in the product_sources table.
"""
from abc import ABC, abstractmethod
import requests, os, json, sqlite3, logging
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "pricesync.db")


# ── Abstract Base ──────────────────────────────────────────────────────────

class EcommerceConnector(ABC):
    """Base class for all e-commerce platform connectors."""

    @abstractmethod
    def authenticate(self) -> bool:
        """Verify credentials are valid. Returns True on success."""
        ...

    @abstractmethod
    def fetch_products(self, limit: int = 100) -> list:
        """Fetch up to *limit* products from the platform."""
        ...


# ── Shopify ────────────────────────────────────────────────────────────────

class ShopifyConnector(EcommerceConnector):
    """Connector for Shopify Admin API (2024-01)."""

    def __init__(self):
        self.shop_url = os.getenv("SHOPIFY_SHOP_URL", "")
        self.access_token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
        self.base_url = f"https://{self.shop_url}/admin/api/2024-01"

    def authenticate(self) -> bool:
        if not self.shop_url or not self.access_token:
            log.warning("Shopify credentials not configured")
            return False
        try:
            r = requests.get(
                f"{self.base_url}/shop.json",
                headers={"X-Shopify-Access-Token": self.access_token},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as exc:
            log.error("Shopify auth failed: %s", exc)
            return False

    def fetch_products(self, limit: int = 100) -> list:
        products, page = [], 1
        while len(products) < limit:
            try:
                r = requests.get(
                    f"{self.base_url}/products.json",
                    headers={"X-Shopify-Access-Token": self.access_token},
                    params={"limit": 250, "page": page},
                    timeout=10,
                )
                if r.status_code != 200:
                    break
                data = r.json().get("products", [])
                products.extend(data)
                if len(data) < 250:
                    break
                page += 1
            except Exception as exc:
                log.error("Shopify fetch page %d failed: %s", page, exc)
                break
        return products[:limit]


# ── WooCommerce ────────────────────────────────────────────────────────────

class WooCommerceConnector(EcommerceConnector):
    """Connector for WooCommerce REST API v3."""

    def __init__(self):
        self.site_url = os.getenv("WOOCOMMERCE_SITE_URL", "")
        self.consumer_key = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
        self.consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")
        self.base_url = f"{self.site_url}/wp-json/wc/v3"

    def authenticate(self) -> bool:
        if not self.site_url or not self.consumer_key:
            log.warning("WooCommerce credentials not configured")
            return False
        try:
            r = requests.get(
                f"{self.base_url}/products",
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                params={"per_page": 1},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as exc:
            log.error("WooCommerce auth failed: %s", exc)
            return False

    def fetch_products(self, limit: int = 100) -> list:
        products, page = [], 1
        while len(products) < limit:
            try:
                r = requests.get(
                    f"{self.base_url}/products",
                    auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                    params={"page": page, "per_page": 100},
                    timeout=10,
                )
                if r.status_code != 200:
                    break
                page_products = r.json()
                if not page_products:
                    break
                products.extend(page_products)
                if len(page_products) < 100:
                    break
                page += 1
            except Exception as exc:
                log.error("WooCommerce fetch page %d failed: %s", page, exc)
                break
        return products[:limit]


# ── Free API ───────────────────────────────────────────────────────────────

class FreeApiConnector(EcommerceConnector):
    """Connector for the Free E-commerce Products API."""

    def __init__(self):
        self.base_url = "https://kolzsticks.github.io/Free-Ecommerce-Products-Api/main/products.json"

    def authenticate(self) -> bool:
        return True

    def fetch_products(self, limit: int = 100) -> list:
        try:
            r = requests.get(self.base_url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data[:limit]
            return []
        except Exception as exc:
            log.error("Free API fetch failed: %s", exc)
            return []


# ── Platzi Fake Store API ──────────────────────────────────────────────────

class PlatziApiConnector(EcommerceConnector):
    """Connector for the stable Platzi Fake Store API."""

    def __init__(self):
        self.base_url = "https://api.escuelajs.co/api/v1/products"

    def authenticate(self) -> bool:
        return True

    def fetch_products(self, limit: int = 20) -> list:
        try:
            r = requests.get(self.base_url, params={"offset": 0, "limit": limit}, timeout=10)
            if r.status_code == 200:
                return r.json()
            return []
        except Exception as exc:
            log.error("Platzi API fetch failed: %s", exc)
            return []


# ── Factory ────────────────────────────────────────────────────────────────

class ConnectorFactory:
    """Create connector instances by platform name."""

    _connectors = {
        "shopify": ShopifyConnector,
        "woocommerce": WooCommerceConnector,
        "free_api": FreeApiConnector,
        "platzi": PlatziApiConnector,
    }

    @classmethod
    def create(cls, platform: str) -> EcommerceConnector:
        connector_class = cls._connectors.get(platform.lower())
        if not connector_class:
            raise ValueError(
                f"Unknown platform: {platform}. "
                f"Supported: {list(cls._connectors.keys())}"
            )
        return connector_class()

    @classmethod
    def available_platforms(cls) -> list:
        return list(cls._connectors.keys())


# ── Normalization helpers ──────────────────────────────────────────────────

def normalize_and_save_products(products: list, platform: str) -> int:
    """Convenience function to normalize and save a list of products."""
    synced = 0
    for product in products:
        normalized = _normalize_product(product, platform)
        insert_product_source(
            normalized.get("external_id", ""),
            platform,
            normalized.get("name", ""),
            normalized.get("description"),
            normalized.get("category"),
            normalized.get("price", 0),
            normalized.get("cost", 0),
            normalized.get("sku"),
            normalized.get("image_url"),
            product,
        )
        synced += 1
    return synced


def _normalize_product(product: dict, platform: str) -> dict:
    """Normalize a raw platform product into a unified schema."""
    if platform == "shopify":
        variant = product.get("variants", [{}])[0]
        return {
            "external_id": str(product.get("id", "")),
            "name": product.get("title", ""),
            "description": product.get("body_html", ""),
            "category": product.get("product_type", "General"),
            "price": float(variant.get("price", 0)),
            "cost": float(variant.get("cost", 0)) if variant.get("cost") else 0,
            "sku": variant.get("sku", ""),
            "image_url": (product.get("image") or {}).get("src", ""),
        }
    elif platform == "woocommerce":
        images = product.get("images", [])
        return {
            "external_id": str(product.get("id", "")),
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "category": ", ".join(c["name"] for c in product.get("categories", [])),
            "price": float(product.get("price", 0) or 0),
            "cost": 0,
            "sku": product.get("sku", ""),
            "image_url": images[0].get("src", "") if images else "",
        }
    elif platform == "free_api":
        price = float(product.get("priceCents", 0)) / 100.0
        return {
            "external_id": str(product.get("id", "")),
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "category": product.get("category", "General"),
            "price": price * 100,  # Scaled for premium tech vibe
            "cost": price * 0.6 * 100,
            "sku": f"FREE-{product.get('id', '')}",
            "image_url": product.get("image", ""),
        }
    elif platform == "platzi":
        # Platzi schema: { id, title, price, description, images[], category: { name } }
        price = float(product.get("price", 0))
        images = product.get("images") or []
        cat = product.get("category") or {}
        
        # Platzi cleanup for images
        img_url = images[0] if images else ""
        if img_url.startswith("["):
            try:
                processed = json.loads(img_url)
                img_url = processed[0] if isinstance(processed, list) else img_url
            except: pass
            
        return {
            "external_id": str(product.get("id", "")),
            "name": product.get("title", ""),
            "description": product.get("description", ""),
            "category": cat.get("name", "General"),
            "price": price * 100,
            "cost": price * 0.7 * 100,
            "sku": f"PLAT-{product.get('id', '')}",
            "image_url": img_url,
        }
    else:
        return {
            "external_id": str(product.get("id", "")),
            "name": product.get("name", ""),
            "category": "General",
            "price": 0,
        }


# ── Database operations ───────────────────────────────────────────────────

def insert_product_source(
    external_id, platform, product_name, description, category,
    current_price, cost_price, sku, image_url, raw_metadata,
):
    """Insert or update a product source record."""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO product_sources
               (external_id, platform, product_name, description, category,
                current_price, cost_price, sku, image_url, raw_metadata,
                last_synced, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                external_id, platform, product_name, description, category,
                current_price, cost_price, sku, image_url,
                json.dumps(raw_metadata, default=str),
                now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_product_sources(platform: str = None) -> list:
    """Retrieve product sources, optionally filtered by platform."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if platform:
            cur = conn.execute(
                "SELECT * FROM product_sources WHERE platform = ?", (platform,)
            )
        else:
            cur = conn.execute("SELECT * FROM product_sources")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ── High-level sync ───────────────────────────────────────────────────────

def sync_products_from_platform(platform: str, limit: int = 100) -> int:
    """Sync products from an e-commerce platform into product_sources."""
    connector = ConnectorFactory.create(platform)
    if not connector.authenticate():
        log.warning("Authentication failed for %s", platform)
        return 0

    products = connector.fetch_products(limit=limit)
    synced = 0

    for product in products:
        normalized = _normalize_product(product, platform)
        insert_product_source(
            normalized.get("external_id", ""),
            platform,
            normalized.get("name", ""),
            normalized.get("description"),
            normalized.get("category"),
            normalized.get("price", 0),
            normalized.get("cost", 0),
            normalized.get("sku"),
            normalized.get("image_url"),
            product,
        )
        synced += 1

    log.info("Synced %d products from %s", synced, platform)
    return synced
