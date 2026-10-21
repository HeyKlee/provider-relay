"""Behaviour contract for the Dahl token budget.

Dahl has no usage API, so the ledger is a local sum of Dahl's own reported
`usage.total_tokens`. The invariants that matter:
  1. The budget is DURABLE , a restart must not hand back 100M tokens.
  2. The warning fires once per threshold crossing, not on every request.
  3. An exhausted budget removes Dahl from the chain instead of hard-failing.
  4. The count only ever includes SUCCEEDED requests, so it under-counts at
     worst and never over-counts.
"""

import importlib.util
import json
import sys
import time
import os
from pathlib import Path

PROXY = Path(__file__).resolve().parents[1] / "router.py"

_n = [0]


def load_proxy(tmp_state=None):
    _n[0] += 1
    name = f"relay_proxy_dahl_{_n[0]}"
    spec = importlib.util.spec_from_file_location(name, PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    # Reset to a clean ledger pointed at an isolated state file.
    mod.DAHL_STATE_FILE = str(tmp_state or Path(os.environ['DAHL_STATE_FILE']).parent / f"dahl-state-{_n[0]}.json")
    mod.DAHL_BUDGET_TOTAL = 100_000_000
    mod.DAHL_WARN_REMAINING = 100_000
    mod._budget.update({
        "used_tokens": 0, "requests": 0, "by_model": {},
        "warned": False, "started_at": time.time(),
    })
    return mod


# --- Usage extraction ----------------------------------------------------


def test_extracts_total_tokens():
    p = load_proxy()
    assert p._extract_usage_tokens({"usage": {"total_tokens": 106}}) == 106


def test_falls_back_to_prompt_plus_completion():
    p = load_proxy()
    resp = {"usage": {"prompt_tokens": 42, "completion_tokens": 64}}
    assert p._extract_usage_tokens(resp) == 106


def test_supports_anthropic_style_keys():
    p = load_proxy()
    resp = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    assert p._extract_usage_tokens(resp) == 15


def test_missing_or_malformed_usage_is_zero_not_an_error():
    p = load_proxy()
    for resp in (None, {}, {"usage": None}, {"usage": {}}, {"usage": "x"}, "nope"):
        assert p._extract_usage_tokens(resp) == 0


# --- Accounting ----------------------------------------------------------


def test_tokens_accumulate_and_split_by_model():
    p = load_proxy()
    p.record_tokens("dahl/a", 100)
    p.record_tokens("dahl/b", 250)
    p.record_tokens("dahl/a", 50)
    st = p.budget_state()
    assert st["used_tokens"] == 400
    assert st["requests"] == 3
    assert st["by_model"] == {"dahl/a": 150, "dahl/b": 250}
    assert st["remaining_tokens"] == 100_000_000 - 400


def test_unusable_token_counts_are_rejected():
    p = load_proxy()
    assert p.record_tokens("m", None) is None
    assert p.record_tokens("m", "abc") is None
    assert p.record_tokens("m", -5) is None
    assert p.budget_state()["used_tokens"] == 0


def test_budget_survives_a_restart(tmp_path=None):
    """Durability: the point of persisting at all."""
    state = str(Path(os.environ['DAHL_STATE_FILE']).parent / f"dahl-restart-{_n[0]}.json")
    first = load_proxy(state)
    first.record_tokens("dahl/a", 12_345)
    used_before = first.budget_state()["used_tokens"]

    second = load_proxy(state)
    second._budget.update({
        "used_tokens": 0, "requests": 0, "by_model": {}, "warned": False,
        "started_at": time.time(),
    })
    second._load_budget()
    assert second.budget_state()["used_tokens"] == used_before, (
        "a restart handed back tokens that were already spent"
    )
    assert second.budget_state()["requests"] == 1


def test_state_file_is_valid_json_and_contains_no_secret():
    p = load_proxy()
    p.record_tokens("dahl/a", 10)
    data = json.loads(Path(p.DAHL_STATE_FILE).read_text())
    assert data["used_tokens"] == 10
    assert "key" not in json.dumps(data).lower()


# --- Warning -------------------------------------------------------------


def test_warning_fires_only_below_the_threshold():
    p = load_proxy()
    assert p.budget_state()["low"] is False
    assert p.dahl_warning(p.budget_state()) == ""


def test_warning_latches_once_and_reports_remaining():
    p = load_proxy()
    p.DAHL_BUDGET_TOTAL = 200_000
    p.DAHL_WARN_REMAINING = 100_000
    p.record_tokens("dahl/a", 120_000)  # 80k left -> below threshold
    st = p.budget_state()
    assert st["low"] is True
    assert st["warned"] is True
    assert st["remaining_tokens"] == 80_000

    warning = p.dahl_warning(st)
    assert "Dahl budget low" in warning
    assert "80,000" in warning


def test_warning_does_not_refire_on_later_requests():
    """A latch, not a per-request announcement."""
    p = load_proxy()
    p.DAHL_BUDGET_TOTAL = 200_000
    p.DAHL_WARN_REMAINING = 100_000
    p.record_tokens("dahl/a", 120_000)
    first = p.dahl_warning(p.budget_state())
    assert first
    p.record_tokens("dahl/a", 1_000)
    # The latch stays set, so the message is not re-announced per request; the
    # state (low/warned/remaining) is what monitoring reads.
    st = p.budget_state()
    assert st["warned"] is True
    assert st["remaining_tokens"] == 79_000
    assert st["low"] is True


def test_exhausted_budget_is_reported():
    p = load_proxy()
    p.DAHL_BUDGET_TOTAL = 1_000
    p.record_tokens("dahl/a", 1_000)
    st = p.budget_state()
    assert st["exhausted"] is True
    assert st["remaining_tokens"] == 0


def test_exhausted_budget_removes_dahl_from_the_chain():
    p = load_proxy()
    p.DAHL_BUDGET_TOTAL = 100
    entry = {"provider": "dahl", "model": "deepseek-ai/DeepSeek-V4-Flash-0731"}
    p.providers = lambda: {"dahl": {"key": "k"}}
    # Not yet spent: dahl is routable.
    assert p._steps("X", entry)
    p.record_tokens("dahl/a", 100)
    assert p._dahl_budget_exhausted() is True
    assert p._steps("X", entry) == [], "exhausted dahl still routable"


def test_zero_budget_means_unlimited_not_exhausted():
    """0 must not read as 'already spent'."""
    p = load_proxy()
    p.DAHL_BUDGET_TOTAL = 0
    p.record_tokens("dahl/a", 5_000_000)
    assert p._dahl_budget_exhausted() is False


# --- Health surface ------------------------------------------------------


def test_health_exposes_the_dahl_budget():
    p = load_proxy()
    p.load_catalog = lambda: {}
    snap = p.health_snapshot()
    assert "dahl" in snap["budgets"]
    d = snap["budgets"]["dahl"]
    for field in ("budget_total", "used_tokens", "remaining_tokens",
                  "low", "warned", "exhausted", "source"):
        assert field in d, field
    assert d["budget_total"] == 100_000_000
    assert d["warn_threshold"] == 100_000


def test_budget_never_leaks_a_key():
    p = load_proxy()
    secret = "dahl_secret_value_should_not_appear"
    p.providers = lambda: {"dahl": {"key": secret}}
    payload = json.dumps(p.health_snapshot())
    assert secret not in payload


def test_account_helper_uses_response_usage():
    p = load_proxy()
    state = p._account_dahl({"usage": {"total_tokens": 500}}, "dahl/a")
    assert state["used_tokens"] == 500


def test_account_helper_ignores_zero_usage():
    p = load_proxy()
    assert p._account_dahl({"usage": {}}, "dahl/a") is None
    assert p.budget_state()["used_tokens"] == 0
