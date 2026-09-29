from __future__ import annotations

import random
from urllib.parse import urlparse

COMMON_CORRECTIONS = {
    "cocking": "cooking",
    "cookng": "cooking",
    "parentng": "parenting",
    "parrenting": "parenting",
    "saas ide": "saas ideas",
}

PROBLEM_TERMS = ("struggles", "complaints", "mistakes", "problems", "frustrations", "workarounds")
EXCLUDED_DOMAINS = {"wikipedia.org", "www.wikipedia.org", "merriam-webster.com", "www.merriam-webster.com", "wiktionary.org", "en.wiktionary.org"}
USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
)
FALLBACK_MARKERS = ("no problem-focused results", "no indexed reddit", "collection unavailable", "api unavailable", "no web results found", "no reddit results found")


def normalize_query(topic: str) -> str:
    words = (topic or "").strip().split()
    corrected = []
    for word in words:
        key = word.lower().strip(".,!?;:")
        replacement = COMMON_CORRECTIONS.get(key, word)
        if word[:1].isupper():
            replacement = replacement.capitalize()
        corrected.append(replacement)
    return " ".join(corrected)


def problem_query(topic: str, *, site: str | None = None) -> str:
    normalized = normalize_query(topic)
    prefix = f"site:{site} " if site else ""
    return f"{prefix}{normalized} {' '.join(PROBLEM_TERMS)}".strip()


def broad_query(topic: str) -> str:
    """Primary query: broad enough to avoid quote/site-operator over-filtering."""
    return f"{normalize_query(topic)} {' '.join(PROBLEM_TERMS)}".strip()


def dynamic_headers(base_user_agent: str) -> dict[str, str]:
    return {"User-Agent": random.choice((base_user_agent, *USER_AGENTS)), "Accept-Language": "en-US,en;q=0.9"}


def is_dictionary_or_definition_url(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    return host in EXCLUDED_DOMAINS or any(host.endswith("." + domain) for domain in EXCLUDED_DOMAINS)


def is_fallback_message(text: str, title: str = "") -> bool:
    haystack = f"{title} {text}".lower()
    return any(marker in haystack for marker in FALLBACK_MARKERS)


__all__ = ["normalize_query", "problem_query", "broad_query", "dynamic_headers", "is_dictionary_or_definition_url", "is_fallback_message"]
