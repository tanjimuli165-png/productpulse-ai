from __future__ import annotations

import re
from typing import List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from app.config import REQUEST_TIMEOUT, USER_AGENT
from app.database.models import MarketplaceGap
from app.pricing import format_price_benchmark
from app.search_utils import broad_query, dynamic_headers


class MarketplaceCollector:
    """Collect lightweight public marketplace signals; never invent missing prices or reviews."""

    def collect(self, topic: str, limit: int = 10) -> List[MarketplaceGap]:
        results = []
        for marketplace in ("Etsy", "Gumroad"):
            direct = self._direct_search(marketplace, topic, limit)
            if not direct:
                direct.extend(self._google_search(marketplace, topic, limit))
            if len(direct) < 3:
                direct.extend(self._indexed_search(marketplace, topic, limit - len(direct)))
            results.extend(self._dedupe(direct, limit))
        return results

    def _direct_search(self, marketplace: str, topic: str, limit: int) -> List[MarketplaceGap]:
        url = f"https://www.etsy.com/search?q={quote_plus(topic)}" if marketplace == "Etsy" else f"https://gumroad.com/discover?query={quote_plus(topic)}"
        try:
            response = requests.get(url, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return self._parse_cards(marketplace, BeautifulSoup(response.text, "html.parser"), limit)
        except requests.RequestException:
            return []

    def _google_search(self, marketplace: str, topic: str, limit: int) -> List[MarketplaceGap]:
        """Broad Google HTML fallback for real marketplace titles and visible price tiers."""
        if limit <= 0:
            return []
        query = f"site:{marketplace.lower()}.com {topic} digital planner template price"
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={min(limit, 10)}"
        try:
            response = requests.get(url, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            seen = set()
            for anchor in soup.select("a"):
                href = anchor.get("href", "")
                text = " ".join(anchor.get_text(" ", strip=True).split())
                if not href.startswith("http") or marketplace.lower() not in href.lower() or len(text) < 8 or text in seen:
                    continue
                seen.add(text)
                parent_text = " ".join(anchor.parent.get_text(" ", strip=True).split()) if anchor.parent else text
                results.append(self._from_text(marketplace, text[:180], parent_text[:500], href, "Google fallback"))
                if len(results) >= limit:
                    break
            return results
        except requests.RequestException:
            return []

    def _indexed_search(self, marketplace: str, topic: str, limit: int) -> List[MarketplaceGap]:
        if limit <= 0:
            return []
        query = f"{broad_query(topic)} {marketplace} digital product template review price"
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = requests.get(url, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for card in soup.select(".result")[:limit]:
                anchor = card.select_one(".result__a")
                snippet = card.select_one(".result__snippet")
                if not anchor:
                    continue
                result_url = anchor.get("href", "")
                if marketplace.lower() not in result_url.lower() and marketplace.lower() not in anchor.get_text(" ", strip=True).lower():
                    continue
                text = snippet.get_text(" ", strip=True) if snippet else ""
                results.append(self._from_text(marketplace, anchor.get_text(" ", strip=True), text, result_url, "indexed search"))
            return results
        except requests.RequestException:
            return []

    def _parse_cards(self, marketplace: str, soup: BeautifulSoup, limit: int) -> List[MarketplaceGap]:
        results = []
        seen = set()
        for selector in ("a", "article", "li"):
            for node in soup.select(selector):
                text = " ".join(node.get_text(" ", strip=True).split())
                nested = node.select_one("a")
                link = node.get("href", "") if node.name == "a" else nested.get("href", "") if nested else ""
                if len(text) < 12 or len(text) > 400 or not link or text in seen:
                    continue
                if not any(token in text.lower() for token in ("template", "planner", "printable", "notion", "spreadsheet", "guide", "digital", "pdf")):
                    continue
                seen.add(text)
                results.append(self._from_text(marketplace, text[:180], text, link, "direct page"))
                if len(results) >= limit:
                    return results
        return results

    @staticmethod
    def _from_text(marketplace: str, title: str, text: str, url: str, method: str) -> MarketplaceGap:
        prices = re.findall(r"(?:\$|USD\s?)(\d+(?:\.\d{1,2})?)", f"{title} {text}", re.I)
        rating_match = re.search(r"([1-5](?:\.\d)?)\s*(?:out of 5|/5|stars?)", text, re.I)
        lower = text.lower()
        formats = [name for name in ("Notion", "Printable", "Excel", "Spreadsheet", "PDF", "Planner", "Template") if name.lower() in lower]
        review_insights = []
        for term, insight in (("confus", "Buyers may need clearer setup instructions."), ("missing", "Missing-feature language suggests an opportunity to bundle the requested feature."), ("difficult", "Difficulty language suggests a simpler onboarding path."), ("wish", "Wish-language suggests an unmet feature or bonus opportunity.")):
            if term in lower:
                review_insights.append(insight)
        observed_price = ", ".join(dict.fromkeys(f"${price}" for price in prices[:4])) or None
        format_name = ", ".join(dict.fromkeys(formats)) or "Unknown"
        benchmark = format_price_benchmark(format_name)
        gap_signal = "Review-derived gap" if review_insights else (
            "Observed price and format signal" if observed_price else "Format benchmark only (estimate)"
        )
        return MarketplaceGap(marketplace=marketplace, title=title, url=url, price=observed_price, price_benchmark=benchmark, format=format_name, rating=rating_match.group(1) if rating_match else "Not found", review_insights=review_insights or [f"No 3-star/4-star review text was exposed by the {method}; validate buyer complaints manually."], gap_signal=gap_signal)

    @staticmethod
    def _dedupe(items: List[MarketplaceGap], limit: int) -> List[MarketplaceGap]:
        output = []
        seen = set()
        for item in items:
            key = item.url or item.title.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
            if len(output) >= limit:
                break
        return output


__all__ = ["MarketplaceCollector"]
