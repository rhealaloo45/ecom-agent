"""Real web scrapers for competitor pricing data."""
import re, logging, random, time, requests
from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from typing import List, Dict, Any

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


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
        """Scrape real Amazon data."""
        query = product_name.replace(" ", "+")
        url = f"https://www.amazon.in/s?k={query}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            for item in soup.find_all('div', {'data-component-type': 's-search-result'})[:5]:
                title_elem = item.find('h2')
                price_elem = item.find('span', class_='a-price-whole')
                if title_elem and price_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    if price:
                        results.append({
                            "source": "Amazon",
                            "price": price,
                            "stock_status": "In Stock",  # Assume in stock if listed
                            "seller_type": "Amazon"
                        })
            log.info("Amazon: scraped %d results for %s", len(results), product_name)
            return results
        except Exception as e:
            log.error("Amazon scrape failed: %s", e)
            return []


class FlipkartAdapter(BaseAdapter):
    source = "Flipkart"

    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        """Scrape real Flipkart data."""
        query = product_name.replace(" ", "%20")
        url = f"https://www.flipkart.com/search?q={query}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            for item in soup.find_all('div', class_='_1AtVbE')[:5]:
                title_elem = item.find('div', class_='_4rR01T') or item.find('a', class_='s1Q9rs')
                price_elem = item.find('div', class_='_30jeq3')
                if title_elem and price_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    if price:
                        results.append({
                            "source": "Flipkart",
                            "price": price,
                            "stock_status": "In Stock",
                            "seller_type": "Flipkart"
                        })
            log.info("Flipkart: scraped %d results for %s", len(results), product_name)
            return results
        except Exception as e:
            log.error("Flipkart scrape failed: %s", e)
            return []


class GoogleShoppingAdapter(BaseAdapter):
    source = "Google Shopping"

    def scrape(self, product_name: str, category: str) -> List[Dict[str, Any]]:
        """Scrape real Google Shopping data."""
        query = product_name.replace(" ", "+")
        url = f"https://www.google.com/search?tbm=shop&q={query}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            for item in soup.find_all('div', class_='sh-dgr__content')[:5]:
                title_elem = item.find('h3', class_='tAxDx')
                price_elem = item.find('span', class_='a8Pemb')
                if title_elem and price_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    if price:
                        results.append({
                            "source": "Google Shopping",
                            "price": price,
                            "stock_status": "In Stock",
                            "seller_type": "Marketplace"
                        })
            log.info("Google Shopping: scraped %d results for %s", len(results), product_name)
            return results
        except Exception as e:
            log.error("Google Shopping scrape failed: %s", e)
            return []


# Registry
ADAPTERS: List[BaseAdapter] = [AmazonAdapter(), FlipkartAdapter(), GoogleShoppingAdapter()]


def scrape_all(product_name: str, category: str) -> List[Dict[str, Any]]:
    """Run all adapters and aggregate results."""
    all_results = []
    for adapter in ADAPTERS:
        try:
            data = adapter.scrape(product_name, category)
            all_results.extend(data)
            log.info("%s returned %d results", adapter.source, len(data))
            time.sleep(random.uniform(1, 3))  # Rate limiting
        except Exception as e:
            log.error("Adapter %s failed: %s", adapter.source, e)
    return all_results