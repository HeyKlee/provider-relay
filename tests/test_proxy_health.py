"""Behaviour contract for Relay /health telemetry.

The point of /health is that a silently-failing tier is visible as data. These
tests assert the relationship between recorded outcomes and reported status,
not any particular snapshot of numbers.
"""

import importlib.util
import sys
import time
from pathlib import Path

PROXY = Path(__file__).resolve().parents[1] / "router.py"


def load_proxy(monkeypatch=None, tmp_path=None):
    spec = importlib.util.spec_from_file_location("relay_proxy_under_test", PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_tier(proxy, provider, model):
    """Record one success and one failure for a route."""
    proxy._record_outcome(provider, model, ok=True, ms=120)
    proxy._record_outcome(provider, model, ok=False, ms=4300, error="HTTP 402")


def test_latency_buckets_are_ordered_and_bounded():
    p = load_proxy()
    assert p._latency_bucket(10) == "<500ms"
    assert p._latency_bucket(900) == "<1000ms"
    assert p._latency_bucket(2500) == "<5000ms"
    assert p._latency_bucket(45000) == ">=30s"


def test_success_rate_reflects_recorded_outcomes():
    p = load_proxy()
    prov, model = "testprov", "testmodel"
    for _ in range(3):
        p._record_outcome(prov, model, ok=True, ms=100)
    p._record_outcome(prov, model, ok=False, ms=200, error="HTTP 401")

    st = p._telemetry[p._breaker_route(prov, model)]
    assert st["attempts"] == 4
    assert st["successes"] == 3
    assert st["failures"] == 1


def test_success_clears_stale_error():
    """A recovering tier must not keep advertising its last error."""
    p = load_proxy()
    prov, model = "p2", "m2"
    p._record_outcome(prov, model, ok=False, ms=100, error="HTTP 401 Invalid key")
    assert p._telemetry[p._breaker_route(prov, model)]["last_error"]

    p._record_outcome(prov, model, ok=True, ms=100)
    st = p._telemetry[p._breaker_route(prov, model)]
    assert st["last_error"] is None
    assert st["last_success_at"] is not None


def test_error_is_truncated_and_never_leaks_long_payloads():
    p = load_proxy()
    huge = "x" * 5000
    p._record_outcome("p3", "m3", ok=False, ms=10, error=huge)
    st = p._telemetry[p._breaker_route("p3", "m3")]
    assert len(st["last_error"]) <= 200


def test_percentiles_return_none_without_samples():
    p = load_proxy()
    assert p._percentile_latency({"buckets": {}}) == {"p50": None, "p95": None}


def test_percentiles_split_around_the_median():
    p = load_proxy()
    st = {"buckets": {"<500ms": 3, "<5000ms": 1}}
    out = p._percentile_latency(st)
    assert out["p50"] == "<500ms"
    assert out["p95"] == "<5000ms"


def test_health_reports_every_tier_in_catalog_order(monkeypatch, tmp_path):
    p = load_proxy()
    catalog = {
        "Relay": {
            "steps": [
                {"provider": "openrouter", "models": ["stealth/a"]},
                {"provider": "xkiro", "models": ["qwen/x"]},
            ]
        }
    }
    monkeypatch.setattr(
        p, "load_catalog", lambda: catalog,
    )
    monkeypatch.setattr(
        p, "providers",
        lambda: {
            "openrouter": {"key": "k"},
            "xkiro": {"key": "k"},
        },
    )
    snap = p.health_snapshot()
    assert [t["tier"] for t in snap["tiers"]] == [1, 2]
    assert [t["provider"] for t in snap["tiers"]] == ["openrouter", "xkiro"]
    assert snap["tiers_total"] == 2


def test_unconfigured_tier_is_visible_as_unconfigured(monkeypatch):
    """A missing key must be reported, not silently counted as untested."""
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [{"provider": "9router", "models": ["m"]}]}},
    )
    monkeypatch.setattr(p, "providers", lambda: {"9router": {"key": ""}})
    snap = p.health_snapshot()
    assert snap["tiers"][0]["status"] == "unconfigured"
    assert snap["tiers"][0]["configured"] is False


def test_failing_tier_degrades_overall_status(monkeypatch):
    """One good tier plus one broken tier is 'degraded', not 'healthy'."""
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {
            "Relay": {"steps": [
                {"provider": "openrouter", "models": ["good"]},
                {"provider": "xkiro", "models": ["bad"]},
            ]}
        },
    )
    monkeypatch.setattr(
        p, "providers",
        lambda: {"openrouter": {"key": "k"}, "xkiro": {"key": "k"}},
    )
    p._record_outcome("openrouter", "good", ok=True, ms=100)
    p._record_outcome("xkiro", "bad", ok=False, ms=9000, error="HTTP 402")

    snap = p.health_snapshot()
    assert snap["status"] == "degraded"
    assert snap["tiers_healthy"] == 1
    assert snap["tiers_degraded"] == 1
    assert snap["tiers"][1]["status"] == "failing"
    assert snap["tiers"][1]["success_rate"] == 0.0


def test_breaker_state_is_reported(monkeypatch):
    """An open breaker must show as circuit_open with a countdown."""
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [{"provider": "freellmapi", "models": ["auto"]}]}},
    )
    monkeypatch.setattr(p, "providers", lambda: {"freellmapi": {"key": "k"}})
    p._record_failure("freellmapi", "auto")
    p._record_failure("freellmapi", "auto")

    snap = p.health_snapshot()
    tier = snap["tiers"][0]
    assert tier["breaker"]["open"] is True
    assert tier["status"] == "circuit_open"
    assert tier["breaker"]["seconds_remaining"] > 0


def test_all_healthy_is_healthy(monkeypatch):
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [
            {"provider": "openrouter", "models": ["a"]},
            {"provider": "xkiro", "models": ["b"]},
        ]}},
    )
    monkeypatch.setattr(
        p, "providers",
        lambda: {"openrouter": {"key": "k"}, "xkiro": {"key": "k"}},
    )
    p._record_outcome("openrouter", "a", ok=True, ms=100)
    p._record_outcome("xkiro", "b", ok=True, ms=200)
    snap = p.health_snapshot()
    assert snap["status"] == "healthy"
    assert snap["active_tier"] == 1


def test_no_healthy_tier_reports_down(monkeypatch):
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [{"provider": "openrouter", "models": ["a"]}]}},
    )
    monkeypatch.setattr(p, "providers", lambda: {"openrouter": {"key": "k"}})
    p._record_outcome("openrouter", "a", ok=False, ms=100, error="HTTP 402")
    assert p.health_snapshot()["status"] == "down"


def test_telemetry_keys_are_per_provider_and_model(monkeypatch):
    """One dead model must not mark its whole provider down: the breaker is
    keyed per route for the same reason."""
    p = load_proxy()
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [
            {"provider": "openrouter", "models": ["dead"]},
            {"provider": "openrouter", "models": ["alive"]},
        ]}},
    )
    monkeypatch.setattr(p, "providers", lambda: {"openrouter": {"key": "k"}})
    p._record_outcome("openrouter", "dead", ok=False, ms=100, error="HTTP 402")
    p._record_outcome("openrouter", "alive", ok=True, ms=100)

    snap = p.health_snapshot()
    assert snap["tiers"][0]["status"] == "failing"
    assert snap["tiers"][1]["status"] == "healthy"


def test_health_never_reports_a_key(monkeypatch):
    """No credential may appear anywhere in the payload."""
    p = load_proxy()
    secret = "fixture-super-secret-value-123456"
    monkeypatch.setattr(
        p, "load_catalog",
        lambda: {"Relay": {"steps": [{"provider": "openrouter", "models": ["a"]}]}},
    )
    monkeypatch.setattr(p, "providers", lambda: {"openrouter": {"key": secret}})
    import json
    payload = json.dumps(p.health_snapshot())
    assert secret not in payload
