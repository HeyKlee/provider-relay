"""Behaviour contract for task-based tier promotion.

Two invariants matter more than any individual routing rule:
  1. Promotion is a REORDER. Every tier stays reachable, so a bad guess can
     only cost latency, never availability.
  2. An unconfident request changes nothing at all.
"""

import importlib.util
import sys
from pathlib import Path

PROXY = Path(__file__).resolve().parents[1] / "router.py"

CHAIN = [
    {"provider": "openrouter", "model": "stealth/space-bunny-alpha"},
    {"provider": "xkiro", "model": "qwen/qwen3.8-max:free"},
    {"provider": "xkiro", "model": "qwen/qwen3-coder-plus:free"},
    {"provider": "xkiro", "model": "qwen/qwen3.7-flash:free"},
    {"provider": "chatgpt", "model": "gpt-5.6-sol"},
    {"provider": "chatgpt", "model": "gpt-6-astra"},
    {"provider": "openrouter", "model": "openrouter/free"},
]


def load_proxy():
    spec = importlib.util.spec_from_file_location("relay_proxy_task", PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._affinity.clear()
    mod._task_mix.clear()
    return mod


def msg(text, role="user"):
    return {"role": role, "content": text}


# --- Classifier -----------------------------------------------------------


def test_short_question_is_short_chat():
    p = load_proxy()
    assert p.classify_request({"messages": [msg("What is Relay?")]}) == "short_chat"


def test_code_question_is_code():
    p = load_proxy()
    assert p.classify_request(
        {"messages": [msg("Please refactor this ```python\ndef f(): pass\n```")]}
    ) == "code"


def test_long_input_is_long_context():
    p = load_proxy()
    big = "x" * (p.LONG_CONTEXT_CHARS + 10)
    assert p.classify_request({"messages": [msg(big)]}) == "long_context"


def test_image_parts_are_vision():
    p = load_proxy()
    payload = {"messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ],
    }]}
    assert p.classify_request(payload) == "vision"


def test_text_only_parts_are_not_vision():
    """A content-parts list of pure text must not be read as an image."""
    p = load_proxy()
    payload = {"messages": [{
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }]}
    assert p.classify_request(payload) != "vision"


def test_code_signal_only_considered_on_latest_user_turn():
    """Earlier code in the thread must not mislabel a later plain question."""
    p = load_proxy()
    payload = {"messages": [
        msg("```python\ndef f(): pass\n```", role="assistant"),
        msg("what did that function do?", role="user"),
    ]}
    assert p.classify_request(payload) == "short_chat"


def test_malformed_payload_yields_no_label():
    p = load_proxy()
    assert p.classify_request({}) is None
    assert p.classify_request({"messages": None}) is None
    assert p.classify_request("nope") is None


# --- Promotion ------------------------------------------------------------


def test_promotion_never_drops_a_tier():
    """The critical availability invariant."""
    p = load_proxy()
    for payload in (
        {"messages": [msg("hi")]},
        {"messages": [msg("```\ndef f(): pass\n```")]},
        {"messages": [msg("x" * (p.LONG_CONTEXT_CHARS + 5))]},
        {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:..."}}]}]},
    ):
        ordered, label = p.promote_for_task(list(CHAIN), payload)
        assert len(ordered) == len(CHAIN), f"tier dropped for label {label}"
        assert {id(s) for s in ordered} == {id(s) for s in CHAIN}


def test_code_promotes_a_coder_model():
    p = load_proxy()
    ordered, label = p.promote_for_task(
        list(CHAIN), {"messages": [msg("write a unit test with pytest")]}
    )
    assert label == "code"
    assert "coder" in ordered[0]["model"], f"coder not promoted: {ordered[0]}"


def test_vision_promotes_a_vision_capable_tier():
    p = load_proxy()
    payload = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:..."}}]}]}
    ordered, label = p.promote_for_task(list(CHAIN), payload)
    assert label == "vision"
    # Vision preference is honoured within the paid tail; it cannot bypass free routes.
    paid = [step for step in ordered if p._paid_step(step)]
    assert paid[0]["model"] == "gpt-6-astra"
    assert all(not p._paid_step(step) for step in ordered[:-len(paid)])


def test_long_context_prefers_a_1m_model():
    p = load_proxy()
    ordered, label = p.promote_for_task(
        list(CHAIN), {"messages": [msg("y" * (p.LONG_CONTEXT_CHARS + 5))]}
    )
    assert label == "long_context"
    assert "3.8-max" in ordered[0]["model"] or "gpt" in ordered[0]["model"]


def test_short_chat_keeps_cheapest_fastest_first():
    p = load_proxy()
    ordered, label = p.promote_for_task(list(CHAIN), {"messages": [msg("hey")]})
    assert label == "short_chat"
    assert ordered[0]["model"] == "openrouter/free"


def test_no_confident_signal_leaves_chain_untouched():
    p = load_proxy()
    # A long, prose-only request with no code and no image: no label.
    prose = "the quick brown fox jumps over the lazy dog. " * 20
    ordered, label = p.promote_for_task(list(CHAIN), {"messages": [msg(prose)]})
    assert label is None
    assert ordered == CHAIN


def test_unmatched_preferences_leave_chain_untouched():
    """If nothing in the chain matches, do not reshuffle and do not claim a label."""
    p = load_proxy()
    unrelated = [{"provider": "whatever", "model": "nope"}]
    ordered, label = p.promote_for_task(list(unrelated), {"messages": [msg("hi")]})
    # The request IS classifiable, but no tier suits it, so no promotion is
    # reported and the configured order stands.
    assert label is None
    assert ordered == unrelated


def test_task_routing_can_be_disabled():
    p = load_proxy()
    p.TASK_ROUTING = False
    ordered, label = p.promote_for_task(list(CHAIN), {"messages": [msg("hi")]})
    assert label is None
    assert ordered == CHAIN


def test_empty_chain_is_safe():
    p = load_proxy()
    ordered, label = p.promote_for_task([], {"messages": [msg("hi")]})
    assert ordered == []


# --- Telemetry -----------------------------------------------------------


def test_task_mix_counts_unclassified_too():
    p = load_proxy()
    p.record_task("code")
    p.record_task(None)
    mix = p.task_mix()
    assert mix["requests"] == 2
    assert mix["by_task"]["code"] == 1
    assert mix["by_task"]["unclassified"] == 1, "silent no-signal case must be visible"
    assert mix["enabled"] is True


def test_short_programming_request_is_code_and_biology_is_chat():
    p = load_proxy()
    assert p.classify_request({'messages': [msg('Write a Python function that adds two numbers.')]}) == 'code'
    assert p.classify_request({'messages': [msg('What is a python snake?')]}) == 'short_chat'
