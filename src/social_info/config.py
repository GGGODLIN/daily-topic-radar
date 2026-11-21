"""sources.yml loader."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SourceConfig:
    id: str
    type: str
    enabled: bool
    tier: int
    language: str = "en"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    defaults: dict[str, Any]
    sources: list[SourceConfig]

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


_TOP_LEVEL_KEYS = {"id", "type", "enabled", "tier", "language"}


def load_config(path: Path) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return Config(defaults={}, sources=[])
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping, got {type(raw).__name__}")

    defaults = raw.get("defaults")
    if defaults is None:
        defaults = {}
    elif not isinstance(defaults, dict):
        raise ValueError(f"config 'defaults' must be a mapping, got {type(defaults).__name__}")

    raw_sources = raw.get("sources")
    if raw_sources is None:
        raw_sources = []
    elif not isinstance(raw_sources, list):
        raise ValueError(f"config 'sources' must be a list, got {type(raw_sources).__name__}")

    sources: list[SourceConfig] = []
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ValueError(f"source entry must be a mapping, got {entry!r}")
        if "id" not in entry or "type" not in entry:
            raise ValueError(f"source entry missing id/type: {entry!r}")
        params = {k: v for k, v in entry.items() if k not in _TOP_LEVEL_KEYS}
        sources.append(SourceConfig(
            id=entry["id"],
            type=entry["type"],
            enabled=entry.get("enabled", True),
            tier=entry.get("tier", 2),
            language=entry.get("language", defaults.get("language_default", "en")),
            params=params,
        ))

    return Config(defaults=defaults, sources=sources)
