from typing import List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.database.models import Evidence
from app.config import REQUEST_TIMEOUT, USER_AGENT
from app.search_utils import broad_query, dynamic_headers, is_dictionary_or_definition_url


class WebCollector(BaseCollector):
    name = "Web"

    def collect(self, topic: str, limit: int = 25) -> List[Evidence]:
        primary_query = broad_query(topic)
        results = self._ddg(primary_query, limit, method="duckduckgo_primary")
        if len(results) < 5:
            fallback_query = f"{primary_query} customer experience pain points"
            fallback = self._ddg(fallback_query, limit, method="duckduckgo_fallback")
            results = self._merge(results, fallback, limit)
        return results

    def _ddg(self, query: str, limit: int, method: str) -> List[Evidence]:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = requests.get(url, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for card in soup.select(".result"):
                anchor = card.select_one(".result__a")
                snippet = card.select_one(".result__snippet")
                if not anchor:
                    continue
                result_url = anchor.get("href", "")
                if is_dictionary_or_definition_url(result_url):
                    continue
                results.append(Evidence(source=self.name, title=anchor.get_text(" ", strip=True), text=snippet.get_text(" ", strip=True) if snippet else anchor.get_text(" ", strip=True), url=result_url, score=1.0, metadata={"query": query, "collection_method": method}))
                if len(results) >= limit:
                    break
            return results
        except requests.RequestException:
            return []

    @staticmethod
    def _merge(first: List[Evidence], second: List[Evidence], limit: int) -> List[Evidence]:
        merged = []
        seen = set()
        for item in [*first, *second]:
            key = item.url or item.title.lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
            if len(merged) >= limit:
                break
        return merged


__all__ = ["WebCollector"]
