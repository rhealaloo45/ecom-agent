"""Pluggable scraper adapters – live web scraping with BeautifulSoup."""
import re, logging, random, time
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS_LIST = [
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
        "Referer": "https://www.google.com/",
    },
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bing.com/",
    },
]


def _get_headers():
    return random.choice(HEADERS_LIST)


def _extract_price(text: str) -> float | None:
    """Extract numeric price from messy text."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("₹", "").replace("$", "").strip()
    m = re.search(r"[\d]+(?:\.[\d]{1,2})?", cleaned)
    return float(m.group()) if m else None


class BaseAdapter(ABC):
    source: str = "unknown"

    @abstractmethod
    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        ...


class AmazonAdapter(BaseAdapter):
    source = "Amazon"

    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        results = []
        query = product_name.replace(" ", "+")
        url = f"https://www.amazon.in/s?k={query}"
        log.info("Amazon scrape: %s", url)
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            items = soup.select("div[data-component-type='s-search-result']")[:5]
            for item in items:
                title_el = item.select_one("h2 a span") or item.select_one("h2 span")
                price_whole = item.select_one("span.a-price-whole")
                price_el = item.select_one("span.a-price span.a-offscreen") or price_whole

                title = title_el.get_text(strip=True) if title_el else None
                price = _extract_price(price_el.get_text() if price_el else "")

                if title and price and price > 100:
                    stock_badge = item.select_one("span.a-color-price")
                    stock = "Low Stock" if stock_badge and "left" in stock_badge.get_text(strip=True).lower() else "In Stock"
                    seller_badge = item.select_one("span.a-size-small.a-color-secondary")
                    seller = seller_badge.get_text(strip=True)[:40] if seller_badge else "Marketplace"

                    results.append({
                        "source": "Amazon",
                        "title": title[:80],
                        "price": price,
                        "stock_status": stock,
                        "seller_type": seller,
                    })
        except Exception as e:
            log.error("Amazon scrape error: %s", e)

        return results


class FlipkartAdapter(BaseAdapter):
    source = "Flipkart"

    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        results = []
        query = product_name.replace(" ", "+")
        url = f"https://www.flipkart.com/search?q={query}"
        log.info("Flipkart scrape: %s", url)
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Flipkart uses dynamic class names; try multiple selectors
            price_selectors = ["div._30jeq3", "div.Nx9bqj", "div._1_WHN1"]
            title_selectors = ["a.IRpwTa", "div._4rR01T", "a.wjcEIp", "div.KzDlHZ"]

            titles, prices = [], []
            for sel in title_selectors:
                titles = soup.select(sel)[:5]
                if titles:
                    break
            for sel in price_selectors:
                prices = soup.select(sel)[:5]
                if prices:
                    break

            for i in range(min(len(titles), len(prices))):
                t = titles[i].get_text(strip=True)
                p = _extract_price(prices[i].get_text())
                if t and p and p > 100:
                    results.append({
                        "source": "Flipkart",
                        "title": t[:80],
                        "price": p,
                        "stock_status": "In Stock",
                        "seller_type": "Flipkart Retail",
                    })
        except Exception as e:
            log.error("Flipkart scrape error: %s", e)

        return results


class GenericScraperAdapter(BaseAdapter):
    """Scrapes Google Shopping for price signals."""
    source = "Google Shopping"

    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        results = []
        query = product_name.replace(" ", "+")
        url = f"https://www.google.com/search?q={query}+price&tbm=shop"
        log.info("Google Shopping scrape: %s", url)
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Google Shopping result cards
            cards = soup.select("div.sh-dgr__gr-auto")[:5]
            if not cards:
                cards = soup.select("div.sh-dlr__list-result")[:5]

            for card in cards:
                title_el = card.select_one("h3") or card.select_one("h4") or card.select_one("a")
                price_el = card.select_one("span.a8Pemb") or card.select_one("span[aria-label*='rice']")

                title = title_el.get_text(strip=True) if title_el else None
                price = _extract_price(price_el.get_text() if price_el else "")

                if title and price and price > 50:
                    seller_el = card.select_one("div.aULzUe") or card.select_one("div.IuHnof")
                    seller = seller_el.get_text(strip=True)[:40] if seller_el else "Online Retailer"
                    results.append({
                        "source": "Google Shopping",
                        "title": title[:80],
                        "price": price,
                        "stock_status": "In Stock",
                        "seller_type": seller,
                    })
        except Exception as e:
            log.error("Google Shopping scrape error: %s", e)

        return results


# Registry
ADAPTERS: List[BaseAdapter] = [AmazonAdapter(), FlipkartAdapter(), GenericScraperAdapter()]


def scrape_all(product_name: str, category: str) -> List[Dict[str, Any]]:
    """Run all adapters and aggregate results."""
    all_results = []
    for adapter in ADAPTERS:
        try:
            data = adapter.scrape(product_name, category)
            all_results.extend(data)
            log.info("%s returned %d results", adapter.source, len(data))
        except Exception as e:
            log.error("Adapter %s failed: %s", adapter.source, e)
        time.sleep(random.uniform(1.0, 2.5))
    return all_results
