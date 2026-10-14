"""Signal ②: one new repo taking off on its own, anywhere on GitHub.

A wave of repos sharing a word shows up days after the idea started. Strata
(Niko1221/Strata) was one repo, born 2026-09-24, at 7.7k stars nine days later; its name
was being searched from 9/26 while no second repo carried it. This signal watches the
repo itself.

GitHub search: repos created in the last `window_days` with at least `min_stars`, two
pages. A repo breaks out when it averages `velocity_min` stars a day since birth, or
gained `delta_min` since the last run's snapshot. For new breakouts the owner's display
name is fetched too, because people search it ("niko strata", not "niko1221").

A repo stays above the bar for days. It becomes a lead term once, the day it first
breaks out (remembered for FLAGGED_DAYS); the report's table lists all of them.
No GitHub token is read or sent. The public API is called without Authorization.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import UA, snap
from .i18n import t

PAGES = 2
TOP = 15
FLAGGED_DAYS = 30
API = "https://api.github.com"


def _get(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=headers), timeout=30) as res:
        return json.load(res)


def repo_term(name: str) -> str:
    """'universal-modder' -> 'universal modder'; 'OpenDots' -> 'opendots'."""
    return re.sub(r"[-_.]+", " ", name).strip().lower()


def score(repos: list[dict], before: dict[str, int] | None, now: datetime,
          velocity_min: int = 200, delta_min: int = 400) -> list[dict]:
    """Repos that break out, fastest first. `repos` items are GitHub search results."""
    out = []
    for r in repos:
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        age = max((now - created).total_seconds() / 86400, 1.0)
        stars = r["stargazers_count"]
        velocity = stars / age
        delta = stars - before[r["full_name"]] if before and r["full_name"] in before else None
        if velocity < velocity_min and (delta is None or delta < delta_min):
            continue
        owner, name = r["full_name"].split("/", 1)
        out.append({"repo": r["full_name"], "owner": owner, "name": name, "stars": stars,
                    "velocity": round(velocity), "delta": delta, "age_days": round(age, 1),
                    "description": (r.get("description") or "")[:120]})
    out.sort(key=lambda x: -max(x["velocity"], x["delta"] or 0))
    return out[:TOP]


def variants(row: dict, display: str) -> list[str]:
    """The ways people end up searching a repo: its name, the owner's handle with it,
    and the owner's display name with it (Strata: 'strata', 'niko1221 strata', 'niko strata')."""
    term = repo_term(row["name"])
    out = [term, f"{row['owner'].lower()} {term}"]
    first = (display or "").split(" ")[0].lower()
    if first and first != row["owner"].lower() and first.isascii() and first.isalpha():
        out.append(f"{first} {term}")
    return out


def breakouts(cfg: dict, state: Path) -> dict:
    c = cfg["breakout"]
    since = (date.today() - timedelta(days=c["window_days"])).isoformat()
    q = urllib.parse.quote(f"created:>={since} stars:>={c['min_stars']}")
    repos: list[dict] = []
    for page in range(1, PAGES + 1):
        items = _get(f"/search/repositories?q={q}&sort=stars&order=desc&per_page=100&page={page}").get("items", [])
        repos += items
        if len(items) < 100:
            break
    path = state / "gh_stars.json.gz"
    before = snap.load(path)
    rows = score(repos, before, datetime.now(UTC), c["velocity_min"], c["delta_min"])
    if repos:
        snap.save(path, {r["full_name"]: r["stargazers_count"] for r in repos})
    flagged_path = state / "gh_flagged.json.gz"
    flagged: dict[str, str] = snap.load(flagged_path) or {}
    today = date.today().isoformat()
    for row in rows:
        row["new"] = row["repo"] not in flagged
        flagged.setdefault(row["repo"], today)
    cutoff = (date.today() - timedelta(days=FLAGGED_DAYS)).isoformat()
    snap.save(flagged_path, {k: v for k, v in flagged.items() if v >= cutoff})
    terms = []
    for row in (r for r in rows if r["new"]):
        try:
            display = _get(f"/users/{row['owner']}").get("name") or ""
        except OSError:
            display = ""
        row["variants"] = variants(row, display)
        gain = t("note_breakout_gain", delta=row["delta"]) if row["delta"] is not None else ""
        terms.append({"term": row["variants"][0], "recent": 1, "variants": row["variants"], "repo": row["repo"],
                      "note": t("note_breakout", repo=row["repo"], stars=row["stars"], velocity=row["velocity"], gain=gain),
                      "examples": [f"{row['repo']}: {row['description']}"]})
    return {"searched": len(repos), "first_run": before is None, "repos": rows, "terms": terms,
            "window_days": c["window_days"]}
