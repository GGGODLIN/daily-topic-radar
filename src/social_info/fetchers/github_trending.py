"""GitHub Trending HTML scraper."""
import httpx
from bs4 import BeautifulSoup

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


def _matches_ai(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def _parse_stars(text: str) -> int:
    """Parse a string like '12,345' or '1.2k' or '900' into int."""
    text = (text or "").strip().replace(",", "")
    if not text:
        return 0
    if text.endswith("k"):
        try:
            return int(float(text[:-1]) * 1000)
        except ValueError:
            return 0
    try:
        return int(text)
    except ValueError:
        return 0


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    languages = source.params.get("languages") or [""]
    since = source.params.get("since", "daily")
    keywords = source.params.get("ai_keywords", [])

    items: list[Item] = []
    now = utcnow()
    for lang in languages:
        url = f"https://github.com/trending/{lang}".rstrip("/") + f"?since={since}"
        resp = await http.get(url, timeout=30.0)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for repo in soup.select("article.Box-row"):
            link_el = repo.select_one("h2 a")
            if not link_el:
                continue
            slug = link_el.get("href", "").strip("/")
            if not slug:
                continue
            full_url = f"https://github.com/{slug}"
            title = slug
            description_el = repo.select_one("p")
            description = description_el.text.strip() if description_el else ""
            if not _matches_ai(f"{title} {description}", keywords):
                continue
            stars_el = repo.select_one('a[href$="/stargazers"]')
            stars = _parse_stars(stars_el.text if stars_el else "")
            items.append(Item(
                title=title,
                url=full_url,
                canonical_url=canonical_url(full_url),
                source="github_trending",
                source_handle=f"trending:{lang or 'all'}",
                source_tier=source.tier,
                posted_at=now,
                fetched_at=now,
                author=slug.split("/")[0],
                excerpt=description[:200],
                language="en",
                engagement={"stars": stars},
            ))
    return items
