"""Behaviour contract for hedged requests.

A hedge races the top tiers and keeps the first ACCEPTABLE answer. The two
invariants that matter:
  1. An empty / reasoning-only answer must never win the race.
  2. If the hedge finds nothing acceptable, behaviour must match a plain
     sequential request (availability is never traded for speed).
"""

import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parents[1] / "router.py"

CHAIN = [
    {"provider": "fast", "model": "a"},
    {"provider": "slow", "model": "b"},
    {"provider": "third", "model": "c"},
]


_module_counter = [0]


def load_proxy():
    # A unique module name per call: several tests bind the same provider names,
    # and a shared spec name would let one test's patched globals leak into the
    # next test's module object.
    _module_counter[0] += 1
    name = f"relay_proxy_hedge_{_module_counter[0]}"
    spec = importlib.util.spec_from_file_location(name, PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def reply(text="OK", reasoning=None):
    msg = {"role": "assistant", "content": text}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": msg}]}


# --- Acceptability -------------------------------------------------------


def test_plain_text_reply_is_acceptable():
    p = load_proxy()
    assert p._acceptable_response(reply("hello"))


def test_none_content_is_not_acceptable():
    p = load_proxy()
    assert not p._acceptable_response(reply(None))


def test_empty_or_whitespace_content_is_not_acceptable():
    p = load_proxy()
    assert not p._acceptable_response(reply(""))
    assert not p._acceptable_response(reply("   \n "))


def test_reasoning_only_reply_is_not_acceptable():
    """Hidden reasoning is not a visible answer."""
    p = load_proxy()
    assert not p._acceptable_response(reply(None, reasoning="I thought about it"))


def test_malformed_responses_are_not_acceptable():
    p = load_proxy()
    for bad in (None, {}, {"choices": []}, {"choices": [{}]}, "text"):
        assert not p._acceptable_response(bad)


# --- Racing --------------------------------------------------------------


def test_fast_head_wins_without_waiting_for_the_hedge():
    p = load_proxy()
    calls = []

    def fake(prov, model, payload):
        calls.append(prov)
        if prov == "fast":
            return reply("fast answer")
        time.sleep(5)  # would blow the test budget if raced
        return reply("slow answer")

    p._call_provider = fake
    t0 = time.time()
    resp, idx, prov, launched = p.hedged_call(list(CHAIN), {}, delay=0.3, max_parallel=2)
    assert time.time() - t0 < 1.0, "hedge waited for the slow tier"
    assert prov == "fast"
    assert idx == 0
    assert launched == 1, "a fast head must not spend a second request"


def test_slow_head_loses_to_the_hedged_tier():
    p = load_proxy()

    def fake(prov, model, payload):
        # "head_slow" is the head-of-chain; the tier the hedge releases is quick.
        if prov == "head_slow":
            time.sleep(0.8)
            return reply("slow head answer")
        return reply("hedged answer")

    p._call_provider = fake
    chain = [{"provider": "head_slow", "model": "a"}, {"provider": "hedged_fast", "model": "b"}]
    t0 = time.time()
    resp, idx, prov, launched = p.hedged_call(chain, {}, delay=0.05, max_parallel=2)
    elapsed = time.time() - t0
    assert prov == "hedged_fast", f"hedged tier did not win: {prov}"
    assert idx == 1
    assert launched == 2, "the hedge was not actually released"
    assert elapsed < 0.7, f"waited for the slow head ({elapsed:.2f}s)"
    assert resp["choices"][0]["message"]["content"] == "hedged answer"


def test_empty_answer_does_not_win_the_race():
    """The core correctness property."""
    p = load_proxy()

    def fake(prov, model, payload):
        if prov == "empty":
            return reply(None)  # fast but useless
        # The useful tier takes long enough that the head genuinely wins the
        # race on speed. Acceptability must still reject the head's answer.
        time.sleep(0.5)
        return reply("real answer")

    p._call_provider = fake
    chain = [{"provider": "empty", "model": "a"}, {"provider": "good", "model": "b"}]
    resp, idx, prov, launched = p.hedged_call(chain, {}, delay=0.05, max_parallel=2)
    assert prov == "good", f"empty answer won: {prov}"
    assert resp["choices"][0]["message"]["content"] == "real answer"


def test_fast_failure_does_not_block_progress():
    p = load_proxy()

    def fake(prov, model, payload):
        if prov == "dead":
            raise RuntimeError("HTTP 401")
        return reply("ok from tier 2")

    p._call_provider = fake
    chain = [{"provider": "dead", "model": "a"}, {"provider": "alive", "model": "b"}]
    resp, idx, prov, launched = p.hedged_call(chain, {}, delay=5.0, max_parallel=2)
    assert prov == "alive", "a dead head-of-chain stalled the request"


def test_all_tiers_failing_raises():
    p = load_proxy()

    def fake(prov, model, payload):
        raise RuntimeError(f"{prov} down")

    p._call_provider = fake
    with pytest.raises(RuntimeError):
        p.hedged_call(list(CHAIN), {}, delay=0.05, max_parallel=3)


def test_single_tier_uses_plain_call():
    p = load_proxy()
    calls = []

    def fake(prov, model, payload):
        calls.append(prov)
        return reply("only")

    p._call_provider = fake
    resp, idx, prov, launched = p.hedged_call([CHAIN[0]], {}, delay=0.1, max_parallel=2)
    assert calls == ["fast"], "harding a one-step chain should be a plain call"
    assert prov == "fast"


def test_max_parallel_one_disables_racing():
    p = load_proxy()
    calls = []

    def fake(prov, model, payload):
        calls.append(prov)
        return reply("ok")

    p._call_provider = fake
    p.hedged_call(list(CHAIN), {}, max_parallel=1)  # returns 4-tuple
    assert calls == ["fast"], "raced despite max_parallel=1"


def test_empty_chain_raises():
    p = load_proxy()
    with pytest.raises(RuntimeError):
        p.hedged_call([], {}, delay=0.05, max_parallel=2)


def test_no_acceptable_answer_fails_instead_of_returning_blank():
    """A blank safety or moderation response cannot complete a chat request."""
    p = load_proxy()

    def fake(prov, model, payload):
        if prov == "empty":
            return reply(None)
        return reply(None)

    p._call_provider = fake
    # A consumer must see a failure so it can use its next provider.
    with pytest.raises(RuntimeError):
        p.hedged_call(list(CHAIN), {}, delay=0.05, max_parallel=2)


# --- Telemetry -----------------------------------------------------------


def test_hedge_stats_report_settings():
    p = load_proxy()
    stats = p.hedge_stats()
    assert stats["enabled"] is True
    assert stats["max_parallel"] == p.HEDGE_MAX_PARALLEL
    assert stats["after_seconds"] == p.HEDGE_AFTER_SECONDS


def test_launch_count_distinguishes_real_duplicate_spend():
    """A fast head answers inside the delay: no second request is sent.

    This is the difference between "the race path ran" and "we actually paid
    for a duplicate", and /health's `launched` counter depends on it.
    """
    p = load_proxy()
    calls = []

    def fake(prov, model, payload):
        calls.append(prov)
        return reply("quick")

    p._call_provider = fake
    _, _, _, launched = p.hedged_call(list(CHAIN), {}, delay=5.0, max_parallel=2)
    assert launched == 1
    assert calls == ["fast"], f"a duplicate request was sent: {calls}"


def test_slow_head_does_launch_a_second_request():
    p = load_proxy()
    calls = []

    def fake(prov, model, payload):
        calls.append(prov)
        if prov == "fast":
            time.sleep(0.4)
        return reply("answered")

    p._call_provider = fake
    chain = [{"provider": "fast", "model": "a"}, {"provider": "second", "model": "b"}]
    _, _, prov, launched = p.hedged_call(chain, {}, delay=0.05, max_parallel=2)
    assert launched == 2, "hedge was not actually released"
    assert "second" in calls


def test_health_includes_hedging_block():
    p = load_proxy()
    import json
    assert '"hedging"' in json.dumps(p.health_snapshot())
