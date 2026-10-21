"""Offline topic radar. Reads the observation pool only; no network or LLM."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from social_info.db import Database
from social_info.fetchers.base import Item
from social_info.observation_pool import ObservationPool
from social_info.vendor import new_term_radar_terms as terms

_HF_NOISE = re.compile(
    r"[-_.](gguf|ggml|awq|gptq|fp8|fp16|bf16|int[48]|mlx|exl2|onnx|uncensored|"
    r"abliterated|instruct|chat|it|base|preview|turbo|lora|q\d\w*|"
    r"\d+(\.\d+)?b|a\d+(\.\d+)?b)(?=[-_.]|$)",
    re.I,
)
_DEFAULT_TYPES = (
    "twitter", "huggingface", "github_search", "github_trending",
    "trendshift", "skills_sh", "rss", "product_hunt",
)


def default_config() -> dict[str, Any]:
    return {
        "types": list(_DEFAULT_TYPES),
        "source_ids": [],
        "min_sources": 2,
        "min_likes": 100,
        "top": 25,
        "exclude_source_ids": [
            "techcrunch_ai", "venturebeat_ai", "theregister_security",
            "arstechnica_security", "thehackernews", "krebs_security",
        ],
    }


def load_radar_config(path: Path) -> dict[str, Any]:
    cfg = default_config()
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg.update(loaded)
    extra = cfg.get("stop_words") or []
    if extra:
        terms.add_stop(list(extra))
    return cfg


def base_name(model_id: str) -> str:
    """Regex suffix strip, not a semantic merge. Copied from upstream hf grouping."""
    name = model_id.split("/")[-1]
    for _ in range(4):
        name = _HF_NOISE.sub("", name)
    return re.sub(r"[-_]+", " ", name).strip().lower()


def _selected(row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if row["source_type"] not in set(cfg["types"]):
        return False
    ids = set(cfg.get("source_ids") or [])
    if ids and row["source_id"] not in ids:
        return False
    return row["source_id"] not in set(cfg.get("exclude_source_ids") or [])


def _likes(row: dict[str, Any]) -> int:
    engagement = json.loads(row.get("engagement_json") or "{}")
    return int(engagement.get("likes") or engagement.get("votes") or 0)


def _account(row: dict[str, Any]) -> str:
    if row.get("source_type") == "twitter" or row.get("source") == "x":
        handle = (row.get("source_handle") or "").lstrip("@").lower()
        if handle:
            return handle
    return row.get("author") or row.get("source_handle") or row["source_id"]


def _text_for(row: dict[str, Any]) -> str:
    body = row.get("content") or row.get("excerpt") or ""
    if row["source_type"] == "huggingface":
        meta = json.loads(row.get("metadata_json") or "{}")
        return str(meta.get("id") or row["title"])
    if row["source_type"] in {"github_search", "github_trending", "trendshift"}:
        return f"{row['title']} {body}"
    if row["source_type"] == "skills_sh":
        return row["title"]
    return f"{row['title']} {body}"


def _created(row: dict[str, Any]) -> datetime | None:
    meta = json.loads(row.get("metadata_json") or "{}")
    raw = meta.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _evidence(row: dict[str, Any]) -> dict[str, str]:
    body = row.get("content") or row.get("excerpt") or ""
    return {
        "url": row["url"],
        "account": _account(row),
        "title": row["title"],
        "body_excerpt": " ".join(body.split())[:240],
        "source_id": row["source_id"],
        "content_status": row["content_status"],
    }


def _names_for(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    text = _text_for(row)
    kind = row["source_type"]
    if kind == "huggingface":
        found = {base_name(_text_for(row))}
        return {n for n in found if n}, found
    if kind in {"github_search", "github_trending", "trendshift", "skills_sh"}:
        found = {row["title"].strip().lower()}
        return {n for n in found if n}, found
    strong = terms.names(text, strong=True)
    return terms.names(text), strong


class RadarHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"terms": {}, "leads": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("terms", {})
            self.data.setdefault("leads", [])

    def seen_before(self, term: str, date: str) -> bool:
        first = self.data["terms"].get(term)
        return bool(first) and first < date

    def remember(self, term: str, date: str, lead: dict[str, Any] | None) -> None:
        self.data["terms"].setdefault(term, date)
        if lead is None:
            return
        self.data["leads"] = [
            row for row in self.data["leads"]
            if not (row["term"] == term and row["date"] == date)
        ]
        self.data["leads"].append(lead)

    def leads_on(self, date: str) -> list[dict[str, Any]]:
        return [row for row in self.data["leads"] if row["date"] == date]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze(pool: ObservationPool, cfg: dict[str, Any], history: RadarHistory, date: str) -> dict[str, Any]:
    start, end = Database._utc_range_for_date(date)
    rows = _latest_rows([
        row for row in pool.observations_between(start, end) if _selected(row, cfg)
    ])
    runs = [
        run for run in pool.runs_between(start, end)
        if run["source_id"] not in set(cfg.get("exclude_source_ids") or [])
    ]
    selected_ids = {row["source_id"] for row in rows}
    health = []
    for run in runs:
        if run["source_type"] not in set(cfg["types"]):
            continue
        if cfg.get("source_ids") and run["source_id"] not in set(cfg["source_ids"]):
            continue
        health.append({
            "source_id": run["source_id"],
            "status": run["status"],
            "partial": bool(run["partial"]),
            "items_fetched": run["items_fetched"],
        })
    failed = [row for row in health if row["status"] == "failed"]
    if not rows:
        return {
            "date": date,
            "status": "no_data",
            "baseline_only": False,
            "limited_input": False,
            "leads": [],
            "health": health,
            "failed": failed,
            "baseline_evidence": [],
            "missing_source_ids": _missing_sources(cfg, health),
            "bootstrap": "no_run_health" if not health else "",
            "note": "No selected observations. Baseline was not recorded.",
        }
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        found, strong = _names_for(row)
        for term in found:
            if not term or term in terms.STOP:
                continue
            slot = grouped.setdefault(term, {
                "accounts": set(), "sources": set(), "kinds": set(), "likes": 0, "strong": False,
                "evidence": [], "github": [],
            })
            account = _account(row)
            if account not in slot["accounts"]:
                slot["likes"] += _likes(row)
            slot["accounts"].add(account)
            slot["sources"].add(row["source_id"])
            slot["kinds"] = slot.get("kinds", set())
            slot["kinds"].add(row["source_type"])
            slot["strong"] = slot["strong"] or term in strong
            if len(slot["evidence"]) < 5:
                slot["evidence"].append(_evidence(row))
            if row["source_type"] in {"github_search", "github_trending"}:
                slot["github"].append(row)
    prior_dates = {
        term: value for term, value in history.data["terms"].items() if value < date
    }
    baseline_only = not prior_dates
    leads = []
    baseline_evidence = []
    min_sources = int(cfg.get("min_sources") or 2)
    min_likes = int(cfg.get("min_likes") or 100)
    for term, slot in grouped.items():
        if baseline_only:
            history.remember(term, date, None)
            baseline_evidence.extend(slot["evidence"][:1])
            continue
        if history.seen_before(term, date):
            continue
        rss_only = slot["kinds"] <= {"rss"}
        hf_only = slot["kinds"] <= {"huggingface"}
        github_only = slot["kinds"] <= {"github_search", "github_trending"}
        trend_only = slot["kinds"] <= {"trendshift"}
        if hf_only or trend_only:
            enough = True
        elif github_only:
            enough = _github_breakout(pool, slot["github"], int(cfg.get("github_window_days") or 14), int(cfg.get("github_delta_min") or 400))
        elif rss_only:
            enough = slot["strong"] and len(slot["sources"]) >= min_sources
        else:
            enough = len(slot["accounts"]) >= min_sources or (
                slot["strong"] and slot["likes"] >= min_likes
            )
        if not enough:
            history.remember(term, date, None)
            continue
        github_note = _github_note(pool, slot["github"])
        lead = {
            "term": term,
            "date": date,
            "sources": sorted(slot["sources"]),
            "accounts": sorted(slot["accounts"]),
            "likes": slot["likes"],
            "evidence": slot["evidence"],
            "github": github_note,
        }
        history.remember(term, date, lead)
        leads.append(lead)
    leads.sort(key=lambda row: (-len(row["sources"]), -row["likes"], row["term"]))
    leads = merge_leads_by_url(leads)[: int(cfg.get("top") or 25)]
    if not baseline_only:
        leads = merge_leads_by_url(history.leads_on(date))[: int(cfg.get("top") or 25)]
    history.save()
    limited = any(
        row["content_status"] in {"legacy_excerpt", "excerpt", "title"} for row in rows
    )
    return {
        "date": date,
        "status": "baseline_only" if baseline_only else "ok",
        "baseline_only": baseline_only,
        "limited_input": limited,
        "leads": leads,
        "health": health,
        "failed": failed,
        "selected_sources": sorted(selected_ids),
        "baseline_evidence": baseline_evidence[:25],
        "note": (
            "First observed day is the baseline. Later appearances can be new."
            if baseline_only else ""
        ),
        "missing_source_ids": _missing_sources(cfg, health),
        "bootstrap": _bootstrap_note(rows),
    }


def merge_leads_by_url(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Name extraction splits one post into many terms; without this the top-25
    cap filled with fragments of 3-4 posts (2026-10-20/21 reports)."""
    merged: list[dict[str, Any]] = []
    by_url: dict[str, dict[str, Any]] = {}
    for lead in leads:
        url = ((lead.get("evidence") or [{}])[0]).get("url") or ""
        prior = by_url.get(url) if url else None
        if prior is None:
            row = {**lead, "evidence": list(lead.get("evidence") or [])}
            merged.append(row)
            if url:
                by_url[url] = row
            continue
        prior.setdefault("aliases", []).append(lead["term"])
        prior["sources"] = sorted(set(prior["sources"]) | set(lead.get("sources") or []))
        prior["accounts"] = sorted(set(prior["accounts"]) | set(lead.get("accounts") or []))
        prior["likes"] = max(prior["likes"], lead.get("likes") or 0)
        seen = {ev["url"] for ev in prior["evidence"]}
        for ev in lead.get("evidence") or []:
            if ev["url"] not in seen and len(prior["evidence"]) < 5:
                prior["evidence"].append(ev)
                seen.add(ev["url"])
        repos = {repo["url"] for repo in prior.get("github") or []}
        prior["github"] = list(prior.get("github") or []) + [
            repo for repo in lead.get("github") or [] if repo["url"] not in repos
        ]
    return merged


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per source, account and URL. Likes come from that latest observation."""
    chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["source_id"], _account(row), row["canonical_url"])
        prior = chosen.get(key)
        if prior is None or row["fetched_at"] >= prior["fetched_at"]:
            chosen[key] = row
    return list(chosen.values())


def _github_breakout(
    pool: ObservationPool, rows: list[dict[str, Any]], window_days: int, delta_min: int,
) -> bool:
    """A repo qualifies from its own created date or a measured star delta.

    No created date and no earlier observation is not treated as growth.
    """
    for row in rows:
        created = _created(row)
        if created is not None:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if (fetched - created).total_seconds() <= window_days * 86400:
                return True
        previous = pool.history_before(row["canonical_url"], row["fetched_at"])
        if previous:
            before = int(json.loads(previous[-1].get("engagement_json") or "{}").get("stars") or 0)
            stars = int(json.loads(row.get("engagement_json") or "{}").get("stars") or 0)
            if stars - before >= delta_min:
                return True
    return False


def _missing_sources(cfg: dict[str, Any], health: list[dict[str, Any]]) -> list[str]:
    wanted = set(cfg.get("source_ids") or [])
    if not wanted:
        return []
    seen = {row["source_id"] for row in health}
    return sorted(wanted - seen)


def _bootstrap_note(rows: list[dict[str, Any]]) -> str:
    if any(row["content_status"] == "legacy_excerpt" for row in rows):
        return "legacy_excerpt"
    if any(row["content_status"] in {"excerpt", "title"} for row in rows):
        return "excerpt_or_title"
    return ""


def _github_note(pool: ObservationPool, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = []
    for row in rows:
        created = _created(row)
        stars = int(json.loads(row.get("engagement_json") or "{}").get("stars") or 0)
        note: dict[str, Any] = {"repo": row["title"], "stars": stars, "url": row["url"]}
        if created is None:
            note["age_days"] = None
        else:
            fetched = datetime.fromisoformat(row["fetched_at"])
            note["age_days"] = round(max((fetched - created).total_seconds() / 86400, 0), 1)
        previous = pool.history_before(row["canonical_url"], row["fetched_at"])
        if previous:
            before = int(json.loads(previous[-1].get("engagement_json") or "{}").get("stars") or 0)
            note["delta"] = stars - before
        notes.append(note)
    return notes


def lead_heading(lead: dict[str, Any], shown: int = 4) -> str:
    """One post can yield 40+ fragment names; the full list stays in the JSON."""
    aliases = lead.get("aliases") or []
    heading = " / ".join([lead["term"], *aliases[:shown]])
    return heading + (f" (+{len(aliases) - shown})" if len(aliases) > shown else "")


def radar_items(report: dict[str, Any]) -> list[Item]:
    if report.get("baseline_only") or report.get("status") == "no_data":
        return []
    day = datetime.strptime(report["date"], "%Y-%m-%d")
    items = []
    for lead in report.get("leads") or []:
        evidence = (lead.get("evidence") or [{}])[0]
        url = evidence.get("url") or ""
        items.append(Item(
            title=lead_heading(lead),
            url=url,
            canonical_url=url,
            source="topic_radar",
            source_handle=",".join(lead.get("sources") or []),
            source_tier=1,
            posted_at=day,
            fetched_at=day,
            author=",".join(lead.get("accounts") or []),
            excerpt=evidence.get("body_excerpt") or "",
            content=evidence.get("body_excerpt") or "",
        ))
    return items


def render_radar_markdown(report: dict[str, Any]) -> str:
    lines = ["## 研究選題雷達", ""]
    status = report.get("status")
    lines.append(f"> status: {status}")
    if report.get("baseline_only"):
        lines.append("> baseline_only: true")
        lines.append("> " + report.get("note", ""))
    if report.get("limited_input"):
        lines.append("> limited_input: legacy_excerpt bootstrap, not full text")
    if report.get("failed"):
        lines.append("> failed sources:")
        for row in report["failed"]:
            partial = " partial" if row.get("partial") else ""
            lines.append(f">   - {row['source_id'] or 'unknown'}: failed{partial}")
    elif not report.get("health"):
        lines.append("> failed sources: unknown (no_run_health)")
    elif status != "no_data":
        lines.append("> failed sources: none")
    missing = report.get("missing_source_ids") or []
    if missing:
        lines.append("> missing source_ids: " + ", ".join(missing))
    if report.get("bootstrap"):
        lines.append("> bootstrap: " + report["bootstrap"])
    unknown = [row for row in report.get("health") or [] if row.get("status") == "unknown"]
    if unknown:
        lines.append("> unknown sources: " + ", ".join(row["source_id"] or "unknown" for row in unknown))
    lines.append("")
    if status == "no_data":
        lines.append(report.get("note") or "No selected observations.")
        lines.append("")
        return "\n".join(lines)
    if report.get("baseline_only"):
        lines.append("Baseline evidence:")
        lines.append("")
        for ev in report.get("baseline_evidence") or []:
            title = ev["title"].replace("[", "\\[").replace("]", "\\]")
            lines.append(f"- [{title}]({ev['url']}) @{ev['account']}")
        lines.append("")
        return "\n".join(lines)
    if not report.get("leads"):
        lines.append("No new names after the baseline.")
        lines.append("")
        return "\n".join(lines)
    for lead in report["leads"]:
        lines.append(f"### {lead_heading(lead).replace('|', '/')}")
        lines.append("")
        lines.append(
            "sources: " + ", ".join(lead.get("sources") or [])
            + " · accounts: " + ", ".join(lead.get("accounts") or [])
        )
        for ev in lead.get("evidence") or []:
            title = ev["title"].replace("[", "\\[").replace("]", "\\]")
            lines.append(f"- [{title}]({ev['url']}) @{ev['account']} ({ev['content_status']})")
            if ev.get("body_excerpt"):
                lines.append("> " + ev["body_excerpt"].replace("\n", " "))
        for repo in lead.get("github") or []:
            age = "age unknown" if repo.get("age_days") is None else f"age {repo['age_days']}d"
            delta = "" if "delta" not in repo else f", delta {repo['delta']}"
            lines.append(f"- repo {repo['repo']}: {repo['stars']} stars, {age}{delta}")
        lines.append("")
    return "\n".join(lines)


def write_radar_reports(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report['date']}.json"
    md_path = out_dir / f"{report['date']}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_radar_markdown(report), encoding="utf-8")
    return json_path, md_path


