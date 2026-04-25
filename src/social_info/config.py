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

    defaults = raw.get("defaults", {})
    raw_sources = raw.get("sources", [])

    sources: list[SourceConfig] = []
    for entry in raw_sources:
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
