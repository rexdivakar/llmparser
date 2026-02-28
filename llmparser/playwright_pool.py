"""Thread-local Playwright pooling to reduce browser startup overhead."""

from __future__ import annotations

import atexit
import contextlib
import threading
from collections import OrderedDict
from typing import Any


class _ThreadState:
    def __init__(self) -> None:
        self.playwright = None
        self.browser = None
        self.contexts: OrderedDict[tuple[Any, ...], Any] = OrderedDict()


class PlaywrightPool:
    def __init__(self, max_contexts: int = 2, enabled: bool = True) -> None:
        self._max_contexts = max(1, max_contexts)
        self._enabled = enabled
        self._local = threading.local()

    def _state(self) -> _ThreadState:
        state = getattr(self._local, "state", None)
        if state is None:
            state = _ThreadState()
            self._local.state = state
        return state

    def _ensure_browser(self, state: _ThreadState) -> None:
        if state.browser is not None:
            return
        from playwright.sync_api import sync_playwright

        state.playwright = sync_playwright().start()
        state.browser = state.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

    def get_context(self, key: tuple[Any, ...], **context_kwargs: Any) -> Any:
        if not self._enabled:
            return None
        state = self._state()
        self._ensure_browser(state)
        if key in state.contexts:
            ctx = state.contexts.pop(key)
            state.contexts[key] = ctx
            return ctx
        ctx = state.browser.new_context(**context_kwargs)
        state.contexts[key] = ctx
        while len(state.contexts) > self._max_contexts:
            _, old = state.contexts.popitem(last=False)
            with contextlib.suppress(Exception):
                old.close()
        return ctx

    def close(self) -> None:
        state = getattr(self._local, "state", None)
        if not state:
            return
        for ctx in list(state.contexts.values()):
            with contextlib.suppress(Exception):
                ctx.close()
        state.contexts.clear()
        if state.browser is not None:
            with contextlib.suppress(Exception):
                state.browser.close()
        if state.playwright is not None:
            with contextlib.suppress(Exception):
                state.playwright.stop()


_GLOBAL_POOL = PlaywrightPool()


def get_playwright_pool() -> PlaywrightPool:
    return _GLOBAL_POOL


@atexit.register
def _close_pool() -> None:
    _GLOBAL_POOL.close()
