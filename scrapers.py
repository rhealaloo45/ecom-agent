"""Real web scrapers for competitor pricing data."""
import re, logging, random, time, requests
from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from typing import List, Dict, Any

log = logging.getLogger(__name__)

HEADERS = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
    },
]


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
            header = random.choice(HEADERS)
            resp = requests.get(url, headers=header, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            # Try multiple selectors
            items = soup.find_all('div', {'data-component-type': 's-search-result'}) or soup.find_all('div', class_='s-result-item')
            for item in items[:5]:
                title_elem = item.find('h2') or item.find('span', class_='a-text-normal')
                price_elem = item.find('span', class_='a-price-whole') or item.find('span', class_='a-offscreen')
                link_elem = item.find('a', href=True)
                stock_elem = item.find('span', string=re.compile(r'out of stock', re.I)) or item.find('span', class_='a-color-price')
                if title_elem and price_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    url = "https://www.amazon.in" + link_elem['href'] if link_elem['href'].startswith('/') else link_elem['href']
                    stock_status = "Out of Stock" if stock_elem and 'out of stock' in stock_elem.get_text(strip=True).lower() else "In Stock"
                    if price:
                        results.append({
                            "source": "Amazon",
                            "price": price,
                            "stock_status": stock_status,
                            "seller_type": "Amazon",
                            "url": url
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
            header = random.choice(HEADERS)
            resp = requests.get(url, headers=header, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            # Try multiple selectors
            items = soup.find_all('div', class_='_1AtVbE') or soup.find_all('div', class_='_13oc-S')
            for item in items[:5]:
                title_elem = item.find('div', class_='_4rR01T') or item.find('a', class_='s1Q9rs') or item.find('a', class_='IRpwTa')
                price_elem = item.find('div', class_='_30jeq3') or item.find('div', class_='_1vC4OE')
                link_elem = item.find('a', href=True)
                stock_elem = item.find('div', string=re.compile(r'out of stock', re.I)) or item.find('span', string=re.compile(r'out of stock', re.I))
                if title_elem and price_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    url = "https://www.flipkart.com" + link_elem['href'] if link_elem['href'].startswith('/') else link_elem['href']
                    stock_status = "Out of Stock" if stock_elem else "In Stock"
                    if price:
                        results.append({
                            "source": "Flipkart",
                            "price": price,
                            "stock_status": stock_status,
                            "seller_type": "Flipkart",
                            "url": url
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
            header = random.choice(HEADERS)
            resp = requests.get(url, headers=header, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            # Try multiple selectors
            items = soup.find_all('div', class_='sh-dgr__content') or soup.find_all('div', class_='sh-dlr__list-result')
            for item in items[:5]:
                title_elem = item.find('h3', class_='tAxDx') or item.find('a', class_='Lq5OHe')
                price_elem = item.find('span', class_='a8Pemb') or item.find('span', class_='T14wmb')
                link_elem = item.find('a', href=True)
                if title_elem and price_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                    url = link_elem['href']
                    if price:
                        results.append({
                            "source": "Google Shopping",
                            "price": price,
                            "stock_status": "In Stock",
                            "seller_type": "Marketplace",
                            "url": url
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
