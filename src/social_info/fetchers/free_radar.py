"""Adapter for the independent free new-term-radar collector."""
import asyncio

from social_info.config import SourceConfig


async def fetch(source: SourceConfig, http=None):
    from social_info.free_radar import collect, to_items

    report = await asyncio.to_thread(collect, source, date=source.params.get("date"))
    failed = [kind for kind, health in report["health"].items() if not health["ok"]]
    if failed:
        raise RuntimeError("free_radar incomplete: " + ", ".join(failed))
    return to_items(report, tier=source.tier)
