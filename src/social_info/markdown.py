"""Markdown rendering for daily digest files."""
from collections import defaultdict
from datetime import datetime

from social_info.fetchers.base import FetchResult, Item

PLATFORM_GROUP_ORDER = [
    ("x", "X / Twitter"),
    ("threads", "Threads"),
    ("reddit", "Reddit"),
    ("hn", "Hacker News"),
    ("github_trending", "GitHub Trending"),
    ("trendshift", "GitHub Rising (Trendshift)"),
    ("product_hunt", "Product Hunt"),
    ("huggingface", "HuggingFace"),
    ("rss_lab", "Lab Blogs & Releases"),
    ("rss_media", "English Tech Media"),
    ("zh_cn", "中文 / 中國"),
    ("wechat", "中文 / 微信公眾號"),
    ("zh_tw", "中文 / 台灣"),
]


def _group_key_for_source(source: str, source_handle: str, language: str) -> str:
    """Bucket a (source, handle, language) triple into one of the platform groups."""
    if language.startswith("zh-TW"):
        return "zh_tw"
    if language.startswith("zh-CN"):
        if source == "wewe_rss":
            return "wechat"
        return "zh_cn"
    if source == "rss":
        if any(k in source_handle for k in ("anthropic", "openai", "google", "mistral", "xai")):
            return "rss_lab"
        return "rss_media"
    return source


def render_item(item: Item) -> str:
    lines = []
    lines.append(f"### [{item.title}]({item.url})")
    lines.append("")

    meta_parts = [
        f"`{item.source}:{item.source_handle}`",
        f"T{item.source_tier}",
        item.posted_at.strftime("%Y-%m-%d %H:%M UTC"),
        item.language,
    ]
    if item.author and item.author != item.source_handle.lstrip("@"):
        meta_parts.append(f"by {item.author}")
    if item.engagement.get("likes"):
        meta_parts.append(f"♥ {item.engagement['likes']}")
    if item.engagement.get("comments"):
        meta_parts.append(f"💬 {item.engagement['comments']}")
    if item.engagement.get("score") and item.source != "x":
        meta_parts.append(f"▲ {item.engagement['score']}")
    lines.append(" · ".join(meta_parts))
    lines.append("")

    if item.excerpt:
        excerpt = item.excerpt.replace("\n", " ").strip()
        lines.append(f"> {excerpt}")
        lines.append("")

    if item.also_appeared_in:
        seen = "; ".join(
            f"{a['source']}:{a['source_handle']}" for a in item.also_appeared_in
        )
        lines.append(f"_also seen at: {seen}_")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_file(
    date: str,
    generated_at: datetime,
    items: list[Item],
    failures: list[FetchResult],
) -> str:
    grouped: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        grouped[_group_key_for_source(it.source, it.source_handle, it.language)].append(it)

    for k in grouped:
        grouped[k].sort(key=lambda x: (x.source_tier, -sum(x.engagement.values())))

    sources_active = len({(i.source, i.source_handle) for i in items})

    lines = [
        f"# AI Daily Digest — {date}",
        "",
        f"> generated_at: {generated_at.isoformat()} (Asia/Taipei)",
        f"> total_items: {len(items)}  |  sources_active: {sources_active}  |  sources_failed: {len(failures)}",
    ]
    if failures:
        lines.append("> failures:")
        for f in failures:
            lines.append(f">   - {f.source_id}: {f.error}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for key, label in PLATFORM_GROUP_ORDER:
        bucket = grouped.get(key, [])
        if not bucket:
            continue
        lines.append(f"## {label} ({len(bucket)} items)")
        lines.append("")
        for it in bucket:
            lines.append(render_item(it))

    return "\n".join(lines)
