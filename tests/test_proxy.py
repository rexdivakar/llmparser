"""Tests for llmparser.proxy (ProxyConfig and ProxyRotator)."""

import pytest

from llmparser.proxy import ProxyConfig, ProxyRotator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rotator(proxies: list[str], rotation: str = "round_robin") -> ProxyRotator:
    return ProxyRotator(ProxyConfig(proxies=proxies, rotation=rotation))


# ---------------------------------------------------------------------------
# Round-robin rotation
# ---------------------------------------------------------------------------

def test_round_robin_cycles():
    rotator = make_rotator(["http://p1:8080", "http://p2:8080", "http://p3:8080"])
    assert rotator.get_proxy() == "http://p1:8080"

    rotator.rotate()
    assert rotator.get_proxy() == "http://p2:8080"

    rotator.rotate()
    assert rotator.get_proxy() == "http://p3:8080"

    # Wraps around
    rotator.rotate()
    assert rotator.get_proxy() == "http://p1:8080"


def test_round_robin_single_proxy():
    rotator = make_rotator(["http://p1:8080"])
    assert rotator.get_proxy() == "http://p1:8080"
    rotator.rotate()
    assert rotator.get_proxy() == "http://p1:8080"


# ---------------------------------------------------------------------------
# Random rotation
# ---------------------------------------------------------------------------

def test_random_rotation_selects_from_pool():
    proxies = [f"http://p{i}:8080" for i in range(5)]
    rotator = make_rotator(proxies, rotation="random")

    seen: set[str] = set()
    for _ in range(50):
        p = rotator.get_proxy()
        assert p in proxies
        if p is not None:
            seen.add(p)

    # With 50 draws from 5 proxies the probability of missing any one
    # proxy is (4/5)^50 ≈ 0.00014 — effectively zero.
    assert len(seen) > 1, "random rotation should draw multiple different proxies"


def test_random_rotate_returns_active_proxy():
    proxies = ["http://p1:8080", "http://p2:8080"]
    rotator = make_rotator(proxies, rotation="random")
    for _ in range(20):
        p = rotator.rotate()
        assert p in proxies


# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------

def test_mark_failed_tracks_consecutive_failures():
    rotator = make_rotator(["http://p1:8080", "http://p2:8080"])
    assert rotator._failures["http://p1:8080"] == 0

    rotator.mark_failed("http://p1:8080")
    assert rotator._failures["http://p1:8080"] == 1

    rotator.mark_failed("http://p1:8080")
    assert rotator._failures["http://p1:8080"] == 2


def test_mark_failed_exhausts_proxy_after_3_failures():
    rotator = make_rotator(["http://p1:8080", "http://p2:8080"])

    for _ in range(3):
        rotator.mark_failed("http://p1:8080")

    # p1 should now be exhausted
    assert rotator._exhausted["http://p1:8080"] is True
    # Only p2 remains active
    assert rotator.get_proxy() == "http://p2:8080"


def test_mark_success_resets_failure_count():
    rotator = make_rotator(["http://p1:8080"])
    rotator.mark_failed("http://p1:8080")
    rotator.mark_failed("http://p1:8080")
    rotator.mark_success("http://p1:8080")
    assert rotator._failures["http://p1:8080"] == 0


def test_mark_failed_unknown_proxy_is_noop():
    """mark_failed with an unregistered proxy must not raise."""
    rotator = make_rotator(["http://p1:8080"])
    rotator.mark_failed("http://not-in-pool:8080")  # should not raise


# ---------------------------------------------------------------------------
# has_proxies
# ---------------------------------------------------------------------------

def test_has_proxies_returns_false_when_all_exhausted():
    proxies = ["http://p1:8080", "http://p2:8080"]
    rotator = make_rotator(proxies)

    for proxy in proxies:
        for _ in range(3):
            rotator.mark_failed(proxy)

    assert rotator.has_proxies() is False


def test_has_proxies_true_while_any_active():
    rotator = make_rotator(["http://p1:8080", "http://p2:8080"])

    for _ in range(3):
        rotator.mark_failed("http://p1:8080")

    # p2 still active
    assert rotator.has_proxies() is True


def test_has_proxies_empty_list():
    rotator = make_rotator([])
    assert rotator.has_proxies() is False


# ---------------------------------------------------------------------------
# rotate returns None when all exhausted
# ---------------------------------------------------------------------------

def test_rotate_returns_none_when_exhausted():
    rotator = make_rotator(["http://p1:8080"])
    for _ in range(3):
        rotator.mark_failed("http://p1:8080")

    assert rotator.rotate() is None
    assert rotator.get_proxy() is None


# ---------------------------------------------------------------------------
# Invalid rotation strategy
# ---------------------------------------------------------------------------

def test_invalid_rotation_raises():
    with pytest.raises(ValueError, match="rotation must be"):
        ProxyRotator(ProxyConfig(proxies=["http://p1:8080"], rotation="banana"))
