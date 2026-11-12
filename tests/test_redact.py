"""Credential-shaped tokens must never reach state.db or the raw markdown.

Synthetic tokens are assembled at runtime on purpose: a literal that looks like a
real provider key would itself trip GitHub push protection on this repo.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_info.__main__ import _free_radar_markdown
from social_info.config import Config, SourceConfig
from social_info.db import Database
from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import splice_radar_section
from social_info.redact import redact_item, redact_result, redact_row, redact_text

ARK = "ark-" + "0123abcd-0123-4abc-8abc-0123456789ab" + "-4be60"
SK = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
GH = "ghp_" + "a" * 36
JWT = "eyJ" + "x" * 12 + "." + "y" * 12 + "." + "z" * 12


@pytest.mark.parametrize(
    "token, kind",
    [(ARK, "ark"), (SK, "sk"), (AWS, "aws"), (GH, "github"), (JWT, "jwt")],
)
def test_known_provider_shapes_are_masked(token, kind):
    out = redact_text(f"分享一下我的 key：{token} 用完記得換")
    assert token not in out
    assert f"[REDACTED-KEY:{kind}]" in out


def test_mask_keeps_surrounding_text():
    assert redact_text(f"> {ARK}\n下一行") == "> [REDACTED-KEY:ark]\n下一行"


@pytest.mark.parametrize(
    "text",
    [
        "task-12345678901234567890 是工單編號",  # prefix inside a longer identifier
        "0123abcd-0123-4abc-8abc-0123456789ab",  # bare uuid without the ark- prefix
        "https://x.test/path?token=abc",  # query-string secrets are the http layer's job
        "",
        "沒有任何 key 的普通貼文",
    ],
)
def test_ordinary_text_is_untouched(text):
    assert redact_text(text) == text


def _item(**overrides) -> Item:
    base = dict(
        title="t",
        url="https://x.test/p",
        canonical_url="https://x.test/p",
        source="linuxdo",
        source_handle="",
        source_tier=2,
        posted_at=datetime(2026, 10, 17, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 17, tzinfo=UTC),
    )
    base.update(overrides)
    return Item(**base)


def test_redact_item_covers_every_text_field():
    item = _item(
        title=f"標題 {SK}",
        excerpt=f"摘要 {ARK}",
        content=f"全文 {AWS}",
        comments=[{"author": "a", "text": f"留言 {GH}"}],
        metadata={"note": f"meta {JWT}", "count": 3},
    )
    out = redact_item(item)
    for token in (SK, ARK, AWS, GH, JWT):
        assert token not in repr(out)
    assert out.metadata["count"] == 3
    assert out.url == item.url
    assert item.excerpt.endswith(ARK), "input item is not mutated"


def test_redact_result_rewrites_items_and_errors():
    result = FetchResult(
        source_id="linuxdo",
        started_at=datetime(2026, 10, 17, tzinfo=UTC),
        ok=False,
        error=f"upstream echoed {SK}",
        items=[_item(excerpt=ARK)],
    )
    out = redact_result(result)
    assert out.items[0].excerpt == "[REDACTED-KEY:ark]"
    err_leaked = SK in out.error
    assert not err_leaked, "error token leaked"
    assert "[REDACTED-KEY:sk]" in out.error


def test_redact_item_masks_author_and_preserves_sk_slug_urls():
    slug_a = "https://www.blocktempo.com/sk-hynix-hbm4-production-2026/"
    slug_b = "https://www.blocktempo.com/sk-telecom-ai-datacenter-2026/"
    item_a = _item(
        author=f"user ({GH})",
        url=slug_a,
        canonical_url=slug_a,
        also_appeared_in=[
            {
                "source": "x",
                "source_handle": f"@{SK}",
                "url": slug_b,
            }
        ],
    )
    out_a = redact_item(item_a)
    author_leaked = GH in out_a.author
    handle_leaked = SK in out_a.also_appeared_in[0]["source_handle"]
    assert not author_leaked, "author token leaked"
    assert not handle_leaked, "also_appeared_in handle token leaked"
    assert out_a.url == slug_a
    assert out_a.canonical_url == slug_a
    assert out_a.also_appeared_in[0]["url"] == slug_b


def test_splice_radar_section_masks_tokens():
    spliced = splice_radar_section("# Raw\n", f"## Radar\n- leak {SK}\n")
    spliced_leaked = SK in spliced
    assert not spliced_leaked, "splice_radar_section token leaked"
    assert "[REDACTED-KEY:sk]" in spliced


def test_db_update_also_appeared_in_masks_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        db.init_schema()
        db.insert_item(_item().to_db_row(item_id="id1", title_hash="h1"))
        db.update_also_appeared_in("id1", json.dumps([{"source": "x", "url": f"https://x.test/{SK}"}]))
        stored = db.conn.execute("SELECT also_appeared_in FROM items WHERE id='id1'").fetchone()[0]
        db.close()
    db_leaked = SK in stored
    assert not db_leaked, "db update_also_appeared_in token leaked"
    assert "[REDACTED-KEY:sk]" in stored


def test_free_radar_markdown_masks_report_json_before_splicing(tmp_path):
    report_dir = tmp_path / "new-term-radar"
    report_dir.mkdir()
    payload = {
        "date": "2026-11-12",
        "health": {"github": {"ok": True, "items": 1}},
        "leads": [{"kind": "github", "term": "acme/leak-demo", "note": f"note {SK}"}],
        "raw_items": [
            {
                "status": "breakout",
                "title": "acme/leak-demo",
                "url": "https://github.com/acme/leak-demo",
                "description": f"accidental key {GH} in repo description",
            }
        ],
    }
    (report_dir / "2026-11-12.json").write_text(json.dumps(payload), encoding="utf-8")
    cfg = Config(
        defaults={},
        sources=[
            SourceConfig(
                id="free_radar",
                type="new_term_radar",
                enabled=True,
                tier=2,
                params={"report_dir": str(report_dir)},
            )
        ],
    )
    md = _free_radar_markdown(cfg, "2026-11-12")
    md_leaked = (GH in md) or (SK in md)
    assert not md_leaked, "free_radar markdown leaked token"
    assert "[REDACTED-KEY:github]" in md
    assert "[REDACTED-KEY:sk]" in md


def test_redact_row_masks_string_values():
    row = redact_row({"id": "x", "excerpt": f"a {SK} b", "source_tier": 2})
    assert row == {"id": "x", "excerpt": "a [REDACTED-KEY:sk] b", "source_tier": 2}


def test_db_insert_is_a_second_guard():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        db.init_schema()
        row = _item(excerpt=f"貼文 {ARK}").to_db_row(item_id="id1", title_hash="h1")
        db.insert_item(row)
        stored = db.conn.execute("SELECT excerpt FROM items WHERE id='id1'").fetchone()[0]
        db.close()
    assert ARK not in stored
    assert "[REDACTED-KEY:ark]" in stored
