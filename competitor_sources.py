"""Smart competitor source selection based on product category.

Maps product categories to the most relevant competitor platforms,
enabling category-optimized scraping for accurate price intelligence.
"""
import logging

log = logging.getLogger(__name__)


class CompetitorSourceMapper:
    """Maps product categories to optimal competitor scraping sources."""

    CATEGORY_SOURCES = {
        "electronics": ["amazon"],
        "laptop": ["amazon", "newegg"],
        "laptops": ["amazon", "newegg"],
        "smartphone": ["amazon", "91mobiles"],
        "smartphones": ["amazon", "91mobiles"],
        "headphone": ["amazon", "ebay"],
        "headphones": ["amazon", "ebay"],
        "camera": ["amazon", "bhphotovideo"],
        "cameras": ["amazon", "bhphotovideo"],
        "clothing": ["amazon", "myntra"],
        "shirt": ["amazon", "myntra"],
        "dress": ["amazon", "myntra"],
        "shoe": ["amazon", "myntra"],
        "shoes": ["amazon", "myntra"],
        "footwear": ["amazon", "myntra"],
        "home": ["amazon", "ikea"],
        "home-appliances": ["amazon", "croma"],
        "furniture": ["amazon", "pepperfry"],
        "kitchen": ["amazon"],
        "book": ["amazon", "bookswagon"],
        "books": ["amazon", "bookswagon"],
        "grocery": ["amazon", "bigbasket"],
        "food": ["amazon"],
    }

    SUGGESTED_ALTERNATIVES = {
        "electronics": ["Croma", "Reliance Digital", "Vijay Sales", "Target"],
        "laptop": ["Dell Store", "HP Store", "Lenovo Store", "Croma"],
        "laptops": ["Dell Store", "HP Store", "Lenovo Store", "Croma"],
        "smartphone": ["Apple Store", "Samsung Store", "Croma", "Vijay Sales"],
        "smartphones": ["Apple Store", "Samsung Store", "Croma", "Vijay Sales"],
        "headphone": ["Headphone Zone", "Croma", "Vijay Sales", "Target", "Bose Store"],
        "headphones": ["Headphone Zone", "Croma", "Vijay Sales", "Target", "Bose Store"],
        "clothing": ["Ajio", "Tata Cliq", "Nykaa Fashion", "Urbanic"],
        "shoe": ["Nike Store", "Adidas Store", "Puma", "Superkicks"],
        "shoes": ["Nike Store", "Adidas Store", "Puma", "Superkicks"],
        "home-appliances": ["Croma", "Reliance Digital", "Vijay Sales"],
        "furniture": ["Urban Ladder", "IKEA", "WoodenStreet", "Home Centre"],
        "book": ["Crossword", "Sapna Online"],
        "books": ["Crossword", "Sapna Online"],
        "grocery": ["Blinkit", "Zepto", "Swiggy Instamart"],
    }

    @classmethod
    def get_suggested_alternatives(cls, category: str) -> list:
        category_lower = category.lower().strip() if category else ""
        if category_lower in cls.SUGGESTED_ALTERNATIVES:
            return cls.SUGGESTED_ALTERNATIVES[category_lower]
        for mapped_cat, alts in cls.SUGGESTED_ALTERNATIVES.items():
            if category_lower in mapped_cat or mapped_cat in category_lower:
                return alts
        return ["Target", "Walmart", "Local Retailers"]

    @classmethod
    def get_sources_for_category(cls, category: str, limit: int = 4) -> list:
        """Return the best competitor sources for a given product category.

        Performs exact match first, then fuzzy substring matching, and
        falls back to a default set of universal sources.
        """
        category_lower = category.lower().strip() if category else ""

        # Exact match
        if category_lower in cls.CATEGORY_SOURCES:
            return cls.CATEGORY_SOURCES[category_lower][:limit]

        # Fuzzy substring match
        for mapped_cat, sources in cls.CATEGORY_SOURCES.items():
            if category_lower in mapped_cat or mapped_cat in category_lower:
                return sources[:limit]

        # Default fallback
        log.info("No specific sources for category '%s'; using defaults", category)
        return ["amazon"][:limit]

    @classmethod
    def add_source_for_category(cls, category: str, source: str):
        """Dynamically register a new source for a category."""
        category_lower = category.lower().strip()
        if category_lower not in cls.CATEGORY_SOURCES:
            cls.CATEGORY_SOURCES[category_lower] = []
        if source not in cls.CATEGORY_SOURCES[category_lower]:
            cls.CATEGORY_SOURCES[category_lower].append(source)
            log.info("Added source '%s' for category '%s'", source, category_lower)

    @classmethod
    def get_all_categories(cls) -> list:
        """Return all registered categories."""
        return list(cls.CATEGORY_SOURCES.keys())
