"""Free new-term radar. Paid AIsa calls are not used.

`date` labels the report and selects the Taipei pool window. Snapshot and
fetched_at use the actual clock. Same-day remote blocks are reused only when
the config values are unchanged; X is always read back from the pool.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from social_info.config import SourceConfig
from social_info.db import TAIPEI, Database
from social_info.fetchers.base import Item
from social_info.observation_pool import ObservationPool
from social_info.url_utils import canonical_url
from social_info.vendor.new_term_radar import breakout, i18n, sitemaps, snap, sources

_BREAKOUT = {"window_days": 14, "min_stars": 300, "velocity_min": 200, "delta_min": 400}


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _taipei_day(moment: datetime) -> str:
    return moment.astimezone(TAIPEI).strftime("%Y-%m-%d")


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _path(params: dict[str, Any], key: str, default: str) -> Path:
    raw = Path(str(params.get(key) or default))
    return raw if raw.is_absolute() else Path.cwd() / raw


def _load_config(path: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    if not path.exists():
        notes.append(f"config missing: {path}")
        raw: dict[str, Any] = {}
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    cfg = {
        "lang": raw.get("lang") or "en",
        "sitemaps": raw.get("sitemaps") or {},
        "breakout": {**_BREAKOUT, **(raw.get("breakout") or {})},
        "stop_words": list(raw.get("stop_words") or []),
        "x_accounts": raw.get("x_accounts") or {"labs": [], "bloggers": []},
    }
    return cfg, notes


def _identity(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "lang": cfg["lang"],
        "sitemaps": cfg["sitemaps"],
        "breakout": cfg["breakout"],
        "stop_words": cfg["stop_words"],
        "x_accounts": cfg["x_accounts"],
    }


def _hf_url(category: str, repo_id: str) -> str:
    if category == "spaces":
        return f"https://huggingface.co/spaces/{repo_id}"
    return f"https://huggingface.co/{repo_id}"


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _merge_terms(old: list[dict] | None, new: list[dict] | None) -> list[dict]:
    by_term = {row["term"]: row for row in old or [] if row.get("term")}
    for row in new or []:
        if row.get("term"):
            by_term.setdefault(row["term"], row)
    return list(by_term.values())


def _keep(old: dict, new: dict) -> dict:
    kept = dict(old)
    kept["preserved"] = True
    kept["this_error"] = new.get("error") or "failed"
    return kept


def _merge_hf(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    if new.get("error") and not old.get("error"):
        return _keep(old, new)
    raw: dict[str, dict[str, dict]] = {}
    for side in (old, new):
        for category, rows in (side.get("raw") or {}).items():
            bucket = raw.setdefault(category, {})
            for row in rows:
                if row.get("id"):
                    bucket[row["id"]] = row
    merged = dict(new)
    merged["raw"] = {key: list(rows.values()) for key, rows in raw.items()}
    merged["terms"] = _merge_terms(old.get("terms"), new.get("terms"))
    merged["first_run"] = bool(new.get("first_run"))
    return merged


def _merge_github(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    if new.get("error") and not old.get("error"):
        return _keep(old, new)
    by_repo: dict[str, dict] = {}
    for side in (old, new):
        for row in side.get("raw_repos") or []:
            name = row.get("full_name") or row.get("repo")
            if name:
                by_repo[name] = row
    merged = dict(new)
    merged["raw_repos"] = list(by_repo.values())
    merged["terms"] = _merge_terms(old.get("terms"), new.get("terms"))
    merged["repos"] = old.get("repos") or []
    merged["repos"].extend(row for row in new.get("repos") or [] if row not in merged["repos"])
    return merged


def _merge_sitemap(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    if new.get("error") and not old.get("error"):
        return _keep(old, new)
    sites = dict(old.get("sites") or {})
    for name, row in (new.get("sites") or {}).items():
        prev = sites.get(name)
        if row.get("error") and prev and not prev.get("error"):
            sites[name] = _keep(prev, row)
            continue
        urls = list(dict.fromkeys((prev or {}).get("new_urls") or []))
        urls.extend(row.get("new_urls") or [])
        merged = dict(row)
        merged["new_urls"] = list(dict.fromkeys(urls))
        merged["new"] = len(merged["new_urls"])
        if prev and prev.get("first_run") and not row.get("first_run"):
            merged["first_run"] = False
        sites[name] = merged
    merged_block = dict(new)
    merged_block["sites"] = sites
    merged_block["terms"] = _merge_terms(old.get("terms"), new.get("terms"))
    return merged_block


def _merge_x(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    if new.get("error") and not old.get("error"):
        return _keep(old, new)
    merged = dict(new)
    merged["terms"] = _merge_terms(old.get("terms"), new.get("terms"))
    merged["first_run"] = bool(new.get("first_run") and old.get("first_run"))
    return merged


def _merge_block(kind: str, old: dict | None, new: dict) -> dict:
    if kind == "hf":
        return _merge_hf(old, new)
    if kind == "github":
        return _merge_github(old, new)
    if kind == "sitemap":
        return _merge_sitemap(old, new)
    return _merge_x(old, new)


def _health_of(kind: str, block: dict) -> dict[str, Any]:
    if block.get("preserved"):
        return {"ok": False, "error": str(block.get("this_error") or "preserved"), "preserved": True}
    if block.get("error"):
        return {"ok": False, "error": str(block["error"])}
    if kind == "hf":
        raw = block.get("raw") or {}
        if not raw.get("models") and not raw.get("spaces") and not block.get("terms"):
            return {"ok": False, "error": "empty"}
    if kind == "github" and not block.get("raw_repos") and not block.get("repos") and not block.get("terms"):
        return {"ok": False, "error": "empty"}
    if kind == "sitemap":
        if block.get("skipped"):
            return {"ok": True, "error": "", "skipped": True}
        sites = block.get("sites") or {}
        failed = [name for name, row in sites.items() if row.get("error")]
        if not sites:
            return {"ok": False, "error": "empty"}
        if failed:
            return {"ok": False, "error": "partial: " + ",".join(failed)}
        preserved = [name for name, row in sites.items() if row.get("preserved")]
        if preserved:
            return {"ok": False, "error": "preserved after failure: " + ",".join(preserved)}
    return {"ok": True, "error": ""}


def _call(kind: str, fn, cfg: dict, state: Path) -> dict:
    try:
        block = fn(cfg, state)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return block if isinstance(block, dict) else {"error": "empty"}


def _github(cfg: dict, state: Path) -> dict:
    captured: list[dict] = []
    original = breakout._get

    def _get(path: str) -> dict:
        response = subprocess.run(
            ["gh", "api", path], capture_output=True, text=True, check=True, timeout=40,
        )
        payload = json.loads(response.stdout)
        if "/search/repositories" in path:
            captured.extend(payload.get("items") or [])
        return payload

    breakout._get = _get
    try:
        block = breakout.breakouts(cfg, state)
    finally:
        breakout._get = original
    block["raw_repos"] = captured
    return block


def _pool_posts(pool_path: Path, label: str) -> list[dict]:
    pool = ObservationPool(pool_path)
    try:
        start, end = Database._utc_range_for_date(label)
        rows = pool.observations_between(start, end)
    finally:
        pool.close()
    posts = []
    for row in rows:
        if row.get("source") != "x" and row.get("source_type") != "twitter":
            continue
        text = row.get("content") or row.get("excerpt") or row.get("title") or ""
        if not text:
            continue
        engagement = json.loads(row.get("engagement_json") or "{}")
        created = _aware(row.get("posted_at") or row["fetched_at"])
        posts.append({
            "account": str(row.get("source_handle") or row.get("author") or "").lstrip("@").lower(),
            "text": text,
            "likes": int(engagement.get("likes") or 0),
            "url": row.get("url") or "",
            "created": created,
        })
    return posts


def _seed_vocab(pool_path: Path, label: str) -> dict[str, str] | None:
    path = pool_path.parent / "topic-radar.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    terms = {term: seen for term, seen in (data.get("terms") or {}).items() if seen < label}
    return terms or None


def _x_block(pool_path: Path, state: Path, moment: datetime, label: str) -> tuple[dict, list[str]]:
    notes: list[str] = []
    posts = _pool_posts(pool_path, label)
    path = state / "x_vocab.json.gz"
    vocab = snap.load(path)
    seeded = False
    if vocab is None:
        vocab = _seed_vocab(pool_path, label)
        seeded = vocab is not None
    first = vocab is None
    if first:
        rows: list[dict] = []
        notes.append("沒有前日 baseline")
        vocab = {}
    else:
        if seeded:
            notes.append("昨日詞表來自 topic-radar，不是 X 原文 baseline")
        cutoff = _aware(Database._utc_range_for_date(label)[1])
        end = min(moment, cutoff)
        rows = sources.novel_names(posts, vocab, end)
    if posts:
        today = _taipei_day(moment)
        cutoff = (datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=sources.VOCAB_DAYS)).isoformat()
        saved = dict(vocab)
        for post in posts:
            for term in sources.names(post["text"]):
                saved[term] = today
        snap.save(path, {term: seen for term, seen in saved.items() if seen >= cutoff})
    if not posts:
        notes.append("這個台北日的 pool 沒有 X 列")
    return {
        "terms": rows, "posts": len(posts), "first_run": first, "seeded": seeded,
    }, notes


def _baseline(blocks: dict[str, dict]) -> dict[str, Any]:
    sites = {}
    for name, row in ((blocks.get("sitemap") or {}).get("sites") or {}).items():
        if row.get("first_run"):
            sites[name] = {"urls": row.get("urls") or 0, "new_urls": []}
    hf = blocks.get("hf") or {}
    return {"sitemap": sites, "hf_first_run": bool(hf.get("first_run")), "x_first_run": bool((blocks.get("x") or {}).get("first_run"))}


def _raw_items(blocks: dict[str, dict], observed_on: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    hf = blocks.get("hf") or {}
    raw = hf.get("raw") or {}
    new_ids = set()
    if not hf.get("first_run"):
        for term in hf.get("terms") or []:
            for example in term.get("examples") or []:
                new_ids.add(str(example).split(" ", 1)[0])
    for category in ("models", "spaces"):
        for row in (raw.get(category) or [])[: sources.HF_TOP]:
            repo_id = row.get("id") or ""
            if not repo_id:
                continue
            proven = repo_id in new_ids and not hf.get("first_run")
            items.append({
                "kind": "hf", "category": category, "title": repo_id,
                "url": _hf_url(category, repo_id),
                "status": "new" if proven else "baseline_candidate",
                "proven_new": proven,
                "note": "新進榜" if proven else "首次榜單觀察，未證新上榜",
                "description": row.get("pipeline_tag") or row.get("sdk") or "",
                "tags": row.get("tags") or [],
                "created_at": row.get("createdAt") or row.get("created_at") or "",
                "downloads": int(row.get("downloads") or 0),
                "trending_score": row.get("trendingScore") or 0,
                "rank": len([item for item in items if item.get("category") == category]) + 1,
                "first_observed": observed_on if proven else "",
                "entry": _hf_url(category, repo_id),
                "likes": int(row.get("likes") or 0),
            })
    scored = {row.get("repo") for row in (blocks.get("github") or {}).get("repos") or []}
    for row in (blocks.get("github") or {}).get("raw_repos") or []:
        name = row.get("full_name") or row.get("repo") or ""
        if not name:
            continue
        url = row.get("html_url") or f"https://github.com/{name}"
        items.append({
            "kind": "github", "title": name, "url": url,
            "status": "lead" if name in scored else "captured",
            "entry": url, "first_observed": observed_on if name in scored else "",
            "stars": int(row.get("stargazers_count") or row.get("stars") or 0),
            "created_at": row.get("created_at") or "",
            "description": row.get("description") or "",
            "page_text": "not_fetched",
        })
    for site, row in ((blocks.get("sitemap") or {}).get("sites") or {}).items():
        if row.get("first_run") or row.get("error"):
            continue
        for url in row.get("new_urls") or []:
            items.append({
                "kind": "sitemap", "site": site, "title": sitemaps.slug_text(url).strip() or url,
                "url": url, "status": "new", "entry": url,
                "first_observed": observed_on, "page_text": "not_fetched",
                "note": "title is the URL slug; the page was not fetched",
            })
    for row in (blocks.get("x") or {}).get("terms") or []:
        post = (row.get("posts") or [{}])[0]
        items.append({
            "kind": "x", "title": row["term"], "url": post.get("url") or "",
            "status": "lead", "entry": post.get("url") or "",
            "first_observed": observed_on, "likes": int(row.get("likes") or 0),
            "note": row.get("note") or "",
        })
    return items


def _leads(blocks: dict[str, dict]) -> list[dict[str, str]]:
    leads = []
    for row in (blocks.get("x") or {}).get("terms") or []:
        leads.append({"kind": "x", "term": row["term"], "note": row.get("note") or ""})
    hf = blocks.get("hf") or {}
    if not hf.get("first_run"):
        for row in hf.get("terms") or []:
            leads.append({"kind": "hf", "term": row["term"], "note": row.get("note") or ""})
    for row in (blocks.get("github") or {}).get("terms") or []:
        leads.append({"kind": "github", "term": row["term"], "note": row.get("note") or ""})
    for row in (blocks.get("sitemap") or {}).get("terms") or []:
        leads.append({"kind": "sitemap", "term": row["term"], "note": row.get("note") or ""})
    return leads


def _remote(cfg: dict, state: Path, transports: dict | None) -> dict[str, dict]:
    chosen = transports or {}
    calls = {
        "hf": chosen.get("hf_new", sources.hf_new),
        "github": chosen.get("breakouts", _github),
        "sitemap": chosen.get("diff", sitemaps.diff),
    }
    return {kind: _call(kind, fn, cfg, state) for kind, fn in calls.items()}


def collect(source: SourceConfig, date: str | None = None, *, now: datetime | None = None,
            transports: dict | None = None) -> dict[str, Any]:
    moment = _now(now)
    label = date or _taipei_day(moment)
    params = source.params
    config_path = _path(params, "config_path", "new-term-radar.json")
    state = _path(params, "state_dir", "data/new-term-radar")
    report_dir = _path(params, "report_dir", "reports/new-term-radar")
    pool_path = _path(params, "pool_path", "data/topic-pool.db")
    state.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    cfg, notes = _load_config(config_path)
    i18n.set_lang(cfg["lang"])
    if cfg["stop_words"]:
        from social_info.vendor.new_term_radar import terms
        terms.add_stop(cfg["stop_words"])
    identity = _identity(cfg)
    cache_path = report_dir / f"{label}.json"
    cached = _read_cache(cache_path)
    reused = bool(cached) and cached.get("date") == label and cached.get("config") == identity
    if reused:
        blocks = dict(cached.get("blocks") or {})
    else:
        blocks = _remote(cfg, state, transports)
        if cached and cached.get("date") == label:
            blocks = {
                kind: _merge_block(kind, (cached.get("blocks") or {}).get(kind), block)
                for kind, block in blocks.items()
            }
    try:
        x_block, x_notes = _x_block(pool_path, state, moment, label)
    except Exception as exc:
        x_block, x_notes = {"error": f"{type(exc).__name__}: {exc}", "terms": []}, []
    notes.extend(x_notes)
    blocks["x"] = _merge_x((cached or {}).get("blocks", {}).get("x") if cached and cached.get("date") == label else None, x_block)
    observed_on = (cached.get("observed_on") if reused else None) or _taipei_day(moment)
    captured_at = (cached.get("captured_at") if reused else None) or moment.isoformat()
    health = {kind: _health_of(kind, block) for kind, block in blocks.items()}
    report = {
        "date": label,
        "captured_at": captured_at,
        "observed_on": observed_on,
        "config": identity,
        "reused": reused,
        "first_run": any(bool((blocks.get(kind) or {}).get("first_run")) for kind in ("hf", "github", "x")),
        "blocks": blocks,
        "baseline": _baseline(blocks),
        "leads": _leads(blocks),
        "raw_items": _raw_items(blocks, observed_on),
        "health": health,
        "limitations": notes,
    }
    cache_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / f"{label}.md").write_text(render_report(report), encoding="utf-8")
    return report


def to_items(report: dict[str, Any], tier: int = 2) -> list[Item]:
    fetched = _naive(_aware(report["captured_at"]))
    items: list[Item] = []
    for row in report.get("raw_items") or []:
        if row.get("kind") == "sitemap" and row.get("status") != "new":
            continue
        if not row.get("url"):
            continue
        posted = fetched
        if row.get("created_at"):
            posted = _naive(_aware(row["created_at"]))
        handle = {
            "hf": f"hf:{row.get('category') or 'models'}",
            "github": "github",
            "sitemap": f"sitemap:{row.get('site') or ''}",
            "x": "x",
        }.get(row.get("kind"), str(row.get("kind") or ""))
        content = str(row.get("description") or "")
        if row.get("tags"):
            content += "\n來源標籤：" + ", ".join(str(tag) for tag in row["tags"])
        items.append(Item(
            title=str(row.get("title") or row["url"]),
            url=row["url"],
            canonical_url=canonical_url(row["url"]),
            source="new_term_radar",
            source_handle=handle,
            source_tier=tier,
            posted_at=posted,
            fetched_at=fetched,
            author=str(row.get("title") or "").split("/")[0],
            excerpt=" · ".join(str(row[key]) for key in ("note", "description") if row.get(key))[:200],
            content=content,
            language="en",
            engagement={
                "likes": int(row.get("likes") or 0),
                "stars": int(row.get("stars") or 0),
            },
            metadata={
                "status": row.get("status") or "",
                "entry": row.get("entry") or row["url"],
                "first_observed": row.get("first_observed") or "",
                "proven_new": bool(row.get("proven_new")),
                "page_text": row.get("page_text") or "",
                "content_kind": "radar_ref",
                "signal_kind": row.get("kind"),
                "tags": row.get("tags") or [],
                "rank": row.get("rank"),
                "trending_score": row.get("trending_score"),
                "created_at": row.get("created_at") or "",
            },
        ))
    return items


def render_report(report: dict[str, Any]) -> str:
    lines = ["## new-term-radar 原工具觀察報告", ""]
    lines.append(f"> date: {report.get('date')} · captured_at: {report.get('captured_at')}")
    lines.append(f"> observed_on: {report.get('observed_on')} · first_run: {report.get('first_run')} · reused: {report.get('reused')}")
    lines.append("")
    lines.append("### health")
    for name, row in (report.get("health") or {}).items():
        state = "ok" if row.get("ok") else "failed"
        extra = " preserved" if row.get("preserved") else ""
        lines.append(f"- {name}: {state}{extra} {row.get('error') or ''}".rstrip())
    lines.append("")
    lines.append("### leads")
    for lead in report.get("leads") or []:
        lines.append(f"- {lead.get('kind')}: {lead.get('term')} {lead.get('note') or ''}".rstrip())
    if not report.get("leads"):
        lines.append("- none")
    lines.append("")
    lines.append("### baseline")
    lines.append(json.dumps(report.get("baseline") or {}, ensure_ascii=False))
    lines.append("")
    lines.append("### raw")
    for row in report.get("raw_items") or []:
        note = " · ".join(str(row[key]) for key in ("note", "description") if row.get(key))
        details = {key: row[key] for key in ("tags", "rank", "trending_score", "created_at") if row.get(key)}
        lines.append(f"- {row.get('status')} [{row.get('title')}]({row.get('url')}) — {note}".rstrip())
        if details:
            lines.append("  " + json.dumps(details, ensure_ascii=False))
    lines.append("")
    lines.append("### limitations")
    for note in report.get("limitations") or []:
        lines.append(f"- {note}")
    if not report.get("limitations"):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect the free new-term radar")
    parser.add_argument("--config", default="new-term-radar.json")
    parser.add_argument("--state-dir", default="data/new-term-radar")
    parser.add_argument("--report-dir", default="reports/new-term-radar")
    parser.add_argument("--pool", default="data/topic-pool.db")
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    source = SourceConfig(
        id="free_radar", type="new_term_radar", enabled=True, tier=2,
        params={
            "config_path": args.config, "state_dir": args.state_dir,
            "report_dir": args.report_dir, "pool_path": args.pool,
        },
    )
    report = collect(source, date=args.date)
    print(json.dumps({
        "date": report["date"], "captured_at": report["captured_at"],
        "reused": report["reused"], "health": report["health"],
        "leads": len(report["leads"]), "raw_items": len(report["raw_items"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
