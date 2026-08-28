import tempfile
from pathlib import Path

from social_info.config import SourceConfig, load_config

SAMPLE_YML = """
defaults:
  language_default: en
  excerpt_max_chars: 200
  fetch_timeout_seconds: 30

sources:
  - id: hn
    type: hn_algolia
    enabled: true
    tier: 1
    keywords: [LLM, AI]
    limit: 30

  - id: reddit_localllama
    type: reddit
    enabled: true
    tier: 1
    subreddit: LocalLLaMA
    time_window: day
    limit: 10

  - id: wechat_qbitai
    type: wewe_rss
    enabled: false
    tier: 1
    account_id: qbitai
    language: zh-CN
"""


def test_load_returns_sourceconfig_objects():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sources.yml"
        p.write_text(SAMPLE_YML)
        cfg = load_config(p)

    assert cfg.defaults["fetch_timeout_seconds"] == 30
    assert len(cfg.sources) == 3

    s0 = cfg.sources[0]
    assert isinstance(s0, SourceConfig)
    assert s0.id == "hn"
    assert s0.type == "hn_algolia"
    assert s0.enabled is True
    assert s0.tier == 1
    assert s0.params["keywords"] == ["LLM", "AI"]


def test_enabled_sources_only_returns_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sources.yml"
        p.write_text(SAMPLE_YML)
        cfg = load_config(p)

    enabled = cfg.enabled_sources()
    assert len(enabled) == 2
    assert all(s.enabled for s in enabled)
    assert "wechat_qbitai" not in {s.id for s in enabled}


def test_36kr_does_not_use_the_challenged_direct_feed():
    # Regression guard for the 2026-08-15 silent failure: 36kr.com/feed answers
    # HTTP 200 with a VolcEngine JS challenge page, so a `type: rss` source there
    # parses 0 entries and lands in sources_empty instead of failing loudly.
    # See the sources.yml comment above 36kr_feed for the revert condition.
    cfg = load_config(Path("sources.yml"))
    src = next(s for s in cfg.sources if s.id == "36kr_feed")

    assert src.type == "rsshub"
    assert src.params["path"].startswith("/36kr/")
    assert "36kr.com/feed" not in str(src.params.get("url", ""))


def test_skills_sh_source_is_enabled_and_registered():
    from social_info.pipeline import FETCHER_REGISTRY

    cfg = load_config(Path("sources.yml"))
    src = next(s for s in cfg.sources if s.id == "skills_sh")

    assert src.type == "skills_sh"
    assert src.enabled is True
    assert src.tier == 1
    assert src.params["limit"] == 25
    assert "skills_sh" in FETCHER_REGISTRY
