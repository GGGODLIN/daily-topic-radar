"""Read-only source health diagnostics and cross-community GitHub resonance radar.

Run:
  uv run python -m social_info.health
  uv run python -m social_info.health --problems-only
  uv run python -m social_info.health --resonance --pool data/topic-pool.db
  uv run python -m social_info.health --json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from social_info._time import utcnow
from social_info.config import load_config

_GH_URL_SLUG_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
_BARE_SLUG_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])")
_COMMUNITY_KINDS = {
    "twitter": "x",
    "x": "x",
    "reddit": "reddit",
    "hn_algolia": "hn",
    "hn": "hn",
    "linuxdo": "linuxdo",
    "v2ex": "v2ex",
}
_GITHUB_SOURCE_TYPES = frozenset({"github_trending", "github_search", "trendshift"})


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_repo_slug(raw: str) -> str:
    slug = raw.strip().strip("/").removesuffix(".git")
    parts = slug.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return ""
    return f"{parts[0]}/{parts[1]}"


def compute_source_health(
    db_path: Path,
    *,
    days: int = 7,
    config_path: Path | None = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    enabled_ids: set[str] | None = None
    all_cfg_ids: set[str] = set()
    if config_path is not None and config_path.is_file():
        cfg = load_config(config_path)
        enabled_ids = {s.id for s in cfg.enabled_sources()}
        all_cfg_ids = {s.id for s in cfg.sources}

    since = (utcnow() - timedelta(days=days)).isoformat()
    conn = _connect_readonly(db_path)
    try:
        runs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fetch_runs WHERE started_at >= ? ORDER BY started_at DESC",
                (since,),
            )
        ]
    finally:
        conn.close()

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_source[run["source"]].append(run)

    candidate_sources = set(by_source.keys())
    if enabled_ids is not None:
        if include_disabled:
            candidate_sources |= all_cfg_ids
        else:
            candidate_sources = (candidate_sources & enabled_ids) | enabled_ids

    out: list[dict[str, Any]] = []
    for source in sorted(candidate_sources):
        rs = by_source.get(source, [])
        ok = sum(1 for r in rs if r["status"] == "ok")
        fail = sum(1 for r in rs if r["status"] != "ok")
        empty = sum(1 for r in rs if r["status"] == "ok" and (r.get("items_fetched") or 0) == 0)
        stale = sum(
            1
            for r in rs
            if r["status"] == "ok"
            and (r.get("items_fetched") or 0) > 0
            and r.get("net_new") == 0
        )
        net_new_total = sum(int(r.get("net_new") or 0) for r in rs if r["status"] == "ok")
        rate = (ok / len(rs) * 100.0) if rs else 0.0
        last_err = next((r["error"] or "" for r in rs if r["status"] != "ok"), "")

        if not rs:
            flag = "no_runs"
        elif fail > 0:
            flag = "failing"
        elif empty > 0:
            flag = "empty"
        elif stale == len(rs):
            flag = "all_stale"
        else:
            flag = "healthy"

        out.append({
            "source": source,
            "enabled": True if enabled_ids is None else (source in enabled_ids),
            "runs": len(rs),
            "ok": ok,
            "fail": fail,
            "empty": empty,
            "stale": stale,
            "net_new": net_new_total,
            "rate": round(rate, 1),
            "status_flag": flag,
            "last_error": " ".join(last_err.splitlines()).strip(),
        })
    return out


def compute_resonance(
    pool_path: Path,
    *,
    days: int = 7,
    limit: int = 30,
) -> list[dict[str, Any]]:
    since = (utcnow() - timedelta(days=days)).isoformat()
    conn = _connect_readonly(pool_path)
    try:
        rows = conn.execute(
            "SELECT source_id, source_type, source, source_handle, url, canonical_url, "
            "title, excerpt, content, engagement_json, fetched_at "
            "FROM observations WHERE fetched_at >= ? ORDER BY fetched_at ASC",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    repo_stats: dict[str, dict[str, Any]] = {}
    community_rows: list[tuple[str, str, str]] = []

    for row in rows:
        stype = row["source_type"] or ""
        src = row["source"] or ""
        handle = row["source_handle"] or ""
        is_gh = stype in _GITHUB_SOURCE_TYPES or (src == "new_term_radar" and handle == "github")
        if is_gh:
            m = _GH_URL_SLUG_RE.search(row["canonical_url"] or row["url"] or "")
            slug = _normalize_repo_slug(m.group(1) if m else (row["title"] or ""))
            if not slug:
                continue
            key = slug.lower()
            eng = json.loads(row["engagement_json"] or "{}")
            stars = int(eng.get("stars") or 0)
            entry = repo_stats.get(key)
            if entry is None:
                entry = {
                    "repo": slug,
                    "url": f"https://github.com/{slug}",
                    "first_stars": stars,
                    "stars_now": stars,
                    "github_sources": set(),
                }
                repo_stats[key] = entry
            else:
                if stars > 0 and entry["first_stars"] == 0:
                    entry["first_stars"] = stars
                if stars > 0:
                    entry["stars_now"] = stars
            entry["github_sources"].add(src or stype)
            continue

        comm = _COMMUNITY_KINDS.get(stype) or _COMMUNITY_KINDS.get(src)
        if comm:
            blob = f"{row['title'] or ''}\n{row['excerpt'] or ''}\n{row['content'] or ''}"
            community_rows.append((comm, row["canonical_url"] or row["url"] or "", blob))

    if not repo_stats:
        return []

    mentions_by_repo: dict[str, set[tuple[str, str]]] = defaultdict(set)
    communities_by_repo: dict[str, set[str]] = defaultdict(set)
    known_keys = set(repo_stats.keys())

    for comm, post_url, blob in community_rows:
        matched_keys: set[str] = set()
        for m in _GH_URL_SLUG_RE.finditer(blob):
            norm = _normalize_repo_slug(m.group(1)).lower()
            if norm in known_keys:
                matched_keys.add(norm)
        for m in _BARE_SLUG_RE.finditer(blob):
            norm = m.group(1).lower()
            if norm in known_keys:
                matched_keys.add(norm)
        for key in matched_keys:
            mentions_by_repo[key].add((comm, post_url))
            communities_by_repo[key].add(comm)

    results: list[dict[str, Any]] = []
    for key, info in repo_stats.items():
        delta = max(0, info["stars_now"] - info["first_stars"])
        comms = sorted(communities_by_repo.get(key, set()))
        mention_count = len(mentions_by_repo.get(key, set()))
        if len(comms) >= 2 or mention_count >= 3:
            signal = "resonant"
        elif mention_count >= 1:
            signal = "discussed"
        elif delta >= 400 or "new_term_radar" in info["github_sources"]:
            signal = "unmentioned_spike"
        else:
            signal = "quiet"

        results.append({
            "repo": info["repo"],
            "url": info["url"],
            "stars_now": info["stars_now"],
            "star_delta": delta,
            "community_mentions": mention_count,
            "communities": comms,
            "github_sources": sorted(info["github_sources"]),
            "signal": signal,
        })

    signal_rank = {"resonant": 0, "discussed": 1, "unmentioned_spike": 2, "quiet": 3}
    results.sort(
        key=lambda r: (
            signal_rank.get(r["signal"], 9),
            -len(r["communities"]),
            -r["community_mentions"],
            -r["star_delta"],
            -r["stars_now"],
            r["repo"].lower(),
        )
    )
    return results[:limit]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only source health and pool resonance diagnostics")
    p.add_argument("--db", type=Path, default=Path("state.db"))
    p.add_argument("--config", type=Path, default=Path("sources.yml"))
    p.add_argument("--pool", type=Path, default=Path("data/topic-pool.db"))
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--include-disabled", action="store_true")
    p.add_argument("--problems-only", action="store_true", help="only show failing, empty, or all-stale sources")
    p.add_argument("--resonance", action="store_true", help="cross-check GitHub star spikes against community mentions")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.resonance:
        if not args.pool.is_file():
            print(f"Observation pool not found: {args.pool}", file=sys.stderr)
            return 2
        resonance = compute_resonance(args.pool, days=args.days, limit=args.limit)
        if args.json:
            print(json.dumps({"days": args.days, "resonance": resonance}, ensure_ascii=False, indent=2))
            return 0
        print(f"=== GitHub Cross-Community Resonance (last {args.days} days) ===")
        print(f"{'repo':<38} {'signal':<18} {'stars':>8} {'+delta':>7} {'posts':>5} {'communities'}")
        for row in resonance:
            comms = ",".join(row["communities"]) or "-"
            print(
                f"{row['repo'][:37]:<38} {row['signal']:<18} {row['stars_now']:>8,} "
                f"{row['star_delta']:>+7,} {row['community_mentions']:>5} {comms}"
            )
        return 0

    if not args.db.is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 2

    rows = compute_source_health(
        args.db,
        days=args.days,
        config_path=args.config,
        include_disabled=args.include_disabled,
    )
    if args.problems_only:
        rows = [r for r in rows if r["status_flag"] != "healthy"]

    if args.json:
        print(json.dumps({"days": args.days, "sources": rows}, ensure_ascii=False, indent=2))
        return 0

    print(f"=== Health report (last {args.days} days) ===")
    print(
        f"{'source':<28} {'runs':>4} {'ok':>4} {'fail':>4} {'empty':>5} "
        f"{'stale':>5} {'new':>6} {'rate':>6}  {'flag':<10} {'last_error'}"
    )
    for r in rows:
        print(
            f"{r['source']:<28} {r['runs']:>4} {r['ok']:>4} {r['fail']:>4} "
            f"{r['empty']:>5} {r['stale']:>5} {r['net_new']:>6} {r['rate']:>5.1f}%  "
            f"{r['status_flag']:<10} {r['last_error'][:48]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
