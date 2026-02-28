"""YAML-based crawl profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


def load_profile(path: str | Path, url: str) -> dict[str, Any]:
    """Load YAML profile and return merged settings for the given URL."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    default = data.get("default", {}) if isinstance(data, dict) else {}
    domains = data.get("domains", {}) if isinstance(data, dict) else {}

    netloc = urlparse(url).netloc.lower()
    best_key = ""
    best_cfg: dict[str, Any] = {}
    if isinstance(domains, dict):
        for key, cfg in domains.items():
            if not isinstance(key, str) or not isinstance(cfg, dict):
                continue
            key_lower = key.lower()
            if (netloc == key_lower or netloc.endswith("." + key_lower)) and (
                len(key_lower) > len(best_key)
            ):
                best_key = key_lower
                best_cfg = cfg

    merged = {}
    if isinstance(default, dict):
        merged.update(default)
    merged.update(best_cfg or {})
    return merged
