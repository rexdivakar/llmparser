"""Language detection helpers."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str | None:
    if not text:
        return None
    sample = text.strip()
    if len(sample) < 40:
        return None
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        code = detect(sample)
    except Exception as exc:
        logger.debug("Language detection failed: %s", exc)
        return None
    return code if code else None
