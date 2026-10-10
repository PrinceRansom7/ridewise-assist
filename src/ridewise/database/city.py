"""
City normalization utilities for RideWise.

Normalizes variant spellings, abbreviations, and whitespace in city names:
- Bengaluru, Bangalore, BLR, bangalore -> Bengaluru
- Mysuru, Mysore -> Mysuru
- Mangaluru, Mangalore -> Mangaluru
"""
from __future__ import annotations

import re
from typing import Optional

CITY_SYNONYMS: dict[str, str] = {
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "blr": "Bengaluru",
    "mysuru": "Mysuru",
    "mysore": "Mysuru",
    "mangaluru": "Mangaluru",
    "mangalore": "Mangaluru",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "pune": "Pune",
}

CANONICAL_CITY_VARIANTS: dict[str, list[str]] = {
    "Bengaluru": ["Bengaluru", "Bangalore", "BLR", "bangalore", "bangalore ", "blr"],
    "Mysuru": ["Mysuru", "Mysore", "mysuru", "mysore"],
    "Mangaluru": ["Mangaluru", "Mangalore", "mangaluru", "mangalore"],
    "Chennai": ["Chennai", "chennai"],
    "Hyderabad": ["Hyderabad", "hyderabad"],
    "Pune": ["Pune", "pune"],
}


def normalize_city(city: Optional[str]) -> str:
    """
    Return the canonical city name for any input or raw database value.
    'Bangalore', 'BLR', 'bangalore ' all return 'Bengaluru'.
    """
    if not city:
        return ""
    cleaned = city.strip().lower()
    return CITY_SYNONYMS.get(cleaned, city.strip().title())


def extract_city_from_text(text: str) -> Optional[str]:
    """
    Search text for any known city name or abbreviation.
    Returns the canonical city name if found, else None.
    """
    lower = text.lower()
    for variant, canonical in CITY_SYNONYMS.items():
        if re.search(rf"\b{re.escape(variant)}\b", lower):
            return canonical
    return None
