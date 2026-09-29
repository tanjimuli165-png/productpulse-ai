from typing import List
import re

import requests
from bs4 import BeautifulSoup
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:  # optional in free/minimal installs
    YouTubeTranscriptApi = None

from app.collectors.base import BaseCollector
from app.database.models import Evidence
from app.config import REQUEST_TIMEOUT, USER_AGENT, YOUTUBE_API_KEY
from app.search_utils import broad_query, dynamic_headers


class YouTubeCollector(BaseCollector):
    name = "YouTube"

    def collect(self, topic: str, limit: int = 25) -> List[Evidence]:
        query = broad_query(topic)
        results = self._api(query, limit) if YOUTUBE_API_KEY else []
        if len(results) < 5:
            fallback = self._search_page(query, limit)
            results = self._merge(results, fallback, limit)
        return self._with_transcripts(results)

    def _api(self, topic: str, limit: int) -> List[Evidence]:
        endpoint = "https://www.googleapis.com/youtube/v3/search"
        try:
            response = requests.get(endpoint, params={"part": "snippet", "q": topic, "type": "video", "maxResults": min(limit, 50), "key": YOUTUBE_API_KEY}, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            return [Evidence(source=self.name, title=i["snippet"]["title"], text=i["snippet"].get("description", ""), url=f"https://www.youtube.com/watch?v={i['id']['videoId']}", published_at=i["snippet"].get("publishedAt"), metadata={"channel": i["snippet"].get("channelTitle", ""), "channel_id": i["snippet"].get("channelId", ""), "video_id": i["id"]["videoId"], "collection_method": "youtube_api"}) for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return []

    def _search_page(self, topic: str, limit: int) -> List[Evidence]:
        url = "https://www.youtube.com/results"
        try:
            response = requests.get(url, params={"search_query": topic}, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            ids = list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', response.text)))[:limit]
            results = []
            for video_id in ids:
                title, description = self._video_metadata(video_id)
                results.append(Evidence(source=self.name, title=title or "YouTube video", text=description, url=f"https://www.youtube.com/watch?v={video_id}", metadata={"video_id": video_id, "collection_method": "youtube_search", "search_query": topic}))
            return results
        except (requests.RequestException, ValueError):
            return []

    @staticmethod
    def _video_metadata(video_id: str) -> tuple[str, str]:
        """Read public OG metadata so search fallback does not emit the search query as a title."""
        try:
            response = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.select_one('meta[property="og:title"]')
            description = soup.select_one('meta[property="og:description"]') or soup.select_one('meta[name="description"]')
            return (title.get("content", "").strip() if title else "", description.get("content", "").strip() if description else "")
        except requests.RequestException:
            return "", ""

    @staticmethod
    def _merge(first: List[Evidence], second: List[Evidence], limit: int) -> List[Evidence]:
        merged = []
        seen = set()
        for item in [*first, *second]:
            key = item.metadata.get("video_id") or item.url
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
            if len(merged) >= limit:
                break
        return merged

    def _with_transcripts(self, evidence: List[Evidence]) -> List[Evidence]:
        enriched = []
        for item in evidence:
            video_id = item.metadata.get("video_id") or self._video_id(item.url)
            if not video_id:
                enriched.append(item)
                continue
            transcript, language, error = self._transcript(video_id)
            metadata = {**item.metadata, "video_id": video_id}
            if transcript:
                item.text = transcript
                metadata.update({"transcript": True, "transcript_language": language})
            else:
                metadata.update({"transcript": False, "transcript_error": error or "No transcript available"})
                if not item.text:
                    item.text = item.title
            item.metadata = metadata
            enriched.append(item)
        return enriched

    @staticmethod
    def _video_id(url: str) -> str:
        match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url or "")
        return match.group(1) if match else ""

    @staticmethod
    def _transcript(video_id: str) -> tuple[str, str, str]:
        if YouTubeTranscriptApi is None:
            return "", "", "Optional dependency youtube-transcript-api is not installed"
        try:
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US"])
            rows = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
            language = getattr(fetched, "language_code", "en")
            text = " ".join(str(row.get("text", "")).strip() for row in rows if row.get("text"))
            return text[:12000], language, ""
        except AttributeError:
            try:
                rows = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"])
                return " ".join(row.get("text", "").strip() for row in rows if row.get("text"))[:12000], "en", ""
            except Exception as exc:
                return "", "", str(exc)
        except Exception as exc:
            return "", "", str(exc)


__all__ = ["YouTubeCollector"]
