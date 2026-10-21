"""Behaviour contract for conversation affinity (prompt-cache pinning).

Reusing a cached prompt prefix per conversation means moving a
thread between tiers mid-conversation invalidates that cache and multiplies
cost. These tests assert that a conversation returns to the same tier, and ,
just as importantly , that pinning can never take the chain down.
"""

import importlib.util
import sys
import time
from pathlib import Path

PROXY = Path(__file__).resolve().parents[1] / "router.py"

CHAIN = [
    {"provider": "openrouter", "model": "stealth/a"},
    {"provider": "xkiro", "model": "qwen/b"},
    {"provider": "chatgpt", "model": "gpt-5.6-sol"},
]


def load_proxy():
    spec = importlib.util.spec_from_file_location("relay_proxy_affinity", PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._affinity.clear()
    return mod


def convo(turns=1, opening="hello there", system="you are an assistant"):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": opening})
    for i in range(turns):
        msgs.append({"role": "assistant", "content": f"answer {i}"})
        msgs.append({"role": "user", "content": f"follow up {i}"})
    return {"model": "Relay", "messages": msgs}


# --- Key stability --------------------------------------------------------


def test_key_is_stable_as_a_conversation_grows():
    """The whole point: later turns must fingerprint identically."""
    p = load_proxy()
    first = p.conversation_key(convo(turns=0))
    later = p.conversation_key(convo(turns=5))
    assert first == later, "conversation key changed as the thread grew"


def test_key_differs_between_conversations():
    p = load_proxy()
    a = p.conversation_key(convo(opening="topic one"))
    b = p.conversation_key(convo(opening="topic two"))
    assert a != b


def test_key_is_none_without_messages():
    p = load_proxy()
    assert p.conversation_key({"model": "X", "messages": []}) is None
    assert p.conversation_key({"model": "X"}) is None


def test_key_handles_content_parts():
    """Clients may send content as a list of parts, not a plain string."""
    p = load_proxy()
    payload = {"messages": [
        {"role": "system", "content": [{"type": "text", "text": "sys prompt"}]},
        {"role": "user", "content": [{"type": "text", "text": "real question"}]},
    ]}
    assert p.conversation_key(payload) is not None


def test_key_ignores_malformed_messages():
    p = load_proxy()
    assert p.conversation_key({"messages": ["nope", 5, None]}) is None


# --- Pinning behaviour ----------------------------------------------------


def test_pinned_tier_moves_to_front():
    p = load_proxy()
    key = p.conversation_key(convo())
    p.affinity_set(key, "xkiro:qwen/b")

    ordered, pinned = p.pin_steps(list(CHAIN), key)
    assert pinned == "xkiro:qwen/b"
    assert ordered[0]["provider"] == "xkiro", "pinned tier was not tried first"


def test_no_affinity_leaves_order_untouched():
    p = load_proxy()
    key = p.conversation_key(convo())
    ordered, pinned = p.pin_steps(list(CHAIN), key)
    assert pinned is None
    assert ordered == CHAIN


def test_pinning_is_not_exclusive_so_failover_still_works():
    """A dead pinned tier must never block the rest of the chain."""
    p = load_proxy()
    key = p.conversation_key(convo())
    p.affinity_set(key, "xkiro:qwen/b")
    ordered, _ = p.pin_steps(list(CHAIN), key)
    assert len(ordered) == len(CHAIN), "a tier was dropped from the chain"
    assert {s["provider"] for s in ordered} == {"openrouter", "xkiro", "chatgpt"}


def test_expired_affinity_is_ignored():
    p = load_proxy()
    p.AFFINITY_TTL = 0.01
    key = p.conversation_key(convo())
    p.affinity_set(key, "chatgpt:gpt-5.6-sol")
    time.sleep(0.05)
    ordered, pinned = p.pin_steps(list(CHAIN), key)
    assert pinned is None
    assert ordered == CHAIN


def test_stale_route_no_longer_in_chain_is_ignored():
    """A tier removed from the catalog must not wedge the conversation."""
    p = load_proxy()
    key = p.conversation_key(convo())
    p.affinity_set(key, "freellmapi:auto")
    ordered, pinned = p.pin_steps(list(CHAIN), key)
    assert pinned is None
    assert ordered == CHAIN


def test_round_trip_returns_to_same_tier():
    """Simulate failover then recovery: the thread must not oscillate."""
    p = load_proxy()
    key = p.conversation_key(convo())

    # Turn 1: tier 1 dies, tier 2 serves. The thread pins to tier 2.
    p.affinity_set(key, "xkiro:qwen/b")
    # Turn 2: tier 1 recovers, but the thread stays on the tier that served it.
    ordered, pinned = p.pin_steps(list(CHAIN), key)
    assert pinned == "xkiro:qwen/b"
    assert ordered[0]["provider"] == "xkiro"


def test_map_is_bounded():
    """A long-lived process must not accumulate conversations forever."""
    p = load_proxy()
    p.AFFINITY_MAX = 5
    for i in range(40):
        p.affinity_set(f"key-{i}", "openrouter:stealth/a")
    assert len(p._affinity) <= 5, f"affinity map grew to {len(p._affinity)}"


def test_set_is_noop_without_key_or_route():
    p = load_proxy()
    p.affinity_set(None, "openrouter:x")
    p.affinity_set("k", None)
    assert p._affinity == {}


def test_stats_reports_pinned_conversations():
    p = load_proxy()
    p.affinity_set(p.conversation_key(convo(opening="a")), "openrouter:stealth/a")
    p.affinity_set(p.conversation_key(convo(opening="b")), "xkiro:qwen/b")
    stats = p.affinity_stats()
    assert stats["pinned_conversations"] == 2
    assert stats["active"] == 2
    assert stats["ttl_s"] == p.AFFINITY_TTL


def test_health_includes_affinity_block():
    p = load_proxy()
    import json
    payload = json.dumps(p.health_snapshot())
    assert '"affinity"' in payload
