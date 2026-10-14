"""Snapshots: yesterday's state of each signal, gzip JSON files in the state directory."""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def load(path: Path):
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
