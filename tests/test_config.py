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
