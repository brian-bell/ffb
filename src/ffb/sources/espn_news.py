"""ESPN NFL headlines: unofficial site JSON plus the public RSS feed.

Endpoints (no auth)::

    GET https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/news?limit=N
    GET https://www.espn.com/espn/rss/nfl/news

The site JSON is unofficial and may drift; ``site.api.espn.com`` is often
blocked. RSS is the public fallback. Headlines are narrative only — they never
enter scoring, consensus, VORP, or lineup math.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import httpx

from ffb.sources.espn import USER_AGENT

log = logging.getLogger(__name__)

NEWS_URL = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/news"
RSS_URL = "https://www.espn.com/espn/rss/nfl/news"
DEFAULT_LIMIT = 50


def snapshot_key() -> str:
    return "espn/news_nfl"


def rss_snapshot_key() -> str:
    return "espn/news_nfl_rss"


def fetch_news(limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Fetch the unofficial ESPN NFL news JSON. Hits the network."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {"limit": limit}
    log.info(
        "api request provider=espn-news method=GET url=%s params=%s",
        NEWS_URL,
        urlencode(params),
    )
    response = httpx.get(NEWS_URL, params=params, headers=headers, timeout=30.0)
    if not response.is_success:
        log.info(
            "api response provider=espn-news status=%s items=unavailable",
            response.status_code,
        )
    response.raise_for_status()
    data = response.json()
    articles = data.get("articles") if isinstance(data, dict) else None
    count = len(articles) if isinstance(articles, list) else "unknown"
    log.info("api response provider=espn-news status=%s items=%s", response.status_code, count)
    return data


def fetch_rss() -> dict[str, str]:
    """Fetch ESPN's NFL RSS and wrap the XML so the snapshot cache stays JSON."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"}
    log.info("api request provider=espn-rss method=GET url=%s", RSS_URL)
    response = httpx.get(RSS_URL, headers=headers, timeout=30.0)
    response.raise_for_status()
    log.info("api response provider=espn-rss status=%s items=xml", response.status_code)
    return {"xml": response.text}


def _stringify(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _web_url(item: dict[str, Any]) -> str | None:
    links = item.get("links")
    if not isinstance(links, dict):
        return None
    web = links.get("web")
    if isinstance(web, dict):
        href = web.get("href")
        if isinstance(href, str) and href.strip():
            return href.strip()
    return None


def _athletes(item: dict[str, Any]) -> list[dict[str, str]]:
    categories = item.get("categories")
    if not isinstance(categories, list):
        return []
    athletes: list[dict[str, str]] = []
    seen: set[str] = set()
    for category in categories:
        if not isinstance(category, dict) or category.get("type") != "athlete":
            continue
        native_id = _stringify(category.get("athleteId"))
        if not native_id or native_id in seen:
            continue
        seen.add(native_id)
        name = category.get("description")
        athletes.append(
            {
                "native_id": native_id,
                "full_name": name.strip() if isinstance(name, str) and name.strip() else "",
            }
        )
    return athletes


def parse_news(raw: Any) -> list[dict[str, Any]]:
    """Parse ESPN site-news articles; malformed rows are skipped."""
    if not isinstance(raw, dict):
        return []
    articles = raw.get("articles")
    if not isinstance(articles, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in articles:
        if not isinstance(item, dict):
            log.debug("skip malformed ESPN news article")
            continue
        native_id = _stringify(item.get("id"))
        headline = item.get("headline")
        if not native_id or not isinstance(headline, str) or not headline.strip():
            continue
        summary = item.get("description")
        published = item.get("published")
        rows.append(
            {
                "native_id": native_id,
                "source": "espn",
                "headline": headline.strip(),
                "summary": summary.strip() if isinstance(summary, str) else "",
                "url": _web_url(item),
                "published_at": published.strip() if isinstance(published, str) else None,
                "athletes": _athletes(item),
            }
        )
    return rows


def _rss_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    text = "".join(node.itertext()).strip()
    return text


def parse_rss(raw: Any) -> list[dict[str, Any]]:
    """Parse the JSON-wrapped ESPN NFL RSS feed; bad XML returns ``[]``."""
    if not isinstance(raw, dict):
        return []
    xml = raw.get("xml")
    if not isinstance(xml, str) or not xml.strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        log.debug("skip malformed ESPN RSS payload")
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    rows: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        headline = _rss_text(item.find("title"))
        if not headline:
            continue
        native_id = _rss_text(item.find("guid")) or _rss_text(item.find("link"))
        if not native_id:
            continue
        rows.append(
            {
                "native_id": native_id,
                "source": "espn_rss",
                "headline": headline,
                "summary": _rss_text(item.find("description")),
                "url": _rss_text(item.find("link")) or None,
                "published_at": _rss_text(item.find("pubDate")) or None,
                "athletes": [],
            }
        )
    return rows
