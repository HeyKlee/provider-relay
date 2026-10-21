"""Behaviour contract for always-on caveman + ponytail directives.

The requirement: these apply to EVERY request on EVERY tier, without the prompt
having to name them. They are prompt directives, not routing signals , routing
on the words "caveman"/"ponytail" would send real terse-mode requests to the
weakest model, which is exactly backwards.

Invariants tested here:
  1. Injection happens for a request that never mentions either by name.
  2. It is applied to the payload, not by mutating the caller's dict.
  3. It is idempotent (a retry cannot stack duplicates).
  4. `full` caveman is opt-in per turn, not sticky for the whole thread.
  5. The client's own system prompt is preserved, and role order is valid.
"""

import importlib.util
import sys
from pathlib import Path

PROXY = Path(__file__).resolve().parents[1] / "router.py"

_n = [0]


def load_proxy():
    _n[0] += 1
    name = f"relay_proxy_directives_{_n[0]}"
    spec = importlib.util.spec_from_file_location(name, PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def payload(text="Explain the bug", system=None, extra=None):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": text})
    for e in extra or []:
        msgs.append(e)
    return {"model": "Relay", "messages": msgs}


# --- Core: always on ----------------------------------------------------


def test_directives_inject_without_being_asked():
    """A plain prompt still gets both directives."""
    p = load_proxy()
    out = p.apply_directives(payload("what time is it"))
    first = out["messages"][0]
    assert first["role"] == "system"
    assert p.DIRECTIVE_MARKER in first["content"]
    assert "Ponytail" in first["content"]
    assert "Caveman" in first["content"]


def test_original_messages_are_preserved():
    p = load_proxy()
    src = payload("hi", system="You are an assistant.", extra=[
        {"role": "assistant", "content": "prev"},
    ])
    out = p.apply_directives(src)
    # Ours is first, then the client's own system prompt, then the rest.
    assert out["messages"][0]["role"] == "system"
    assert p.DIRECTIVE_MARKER in out["messages"][0]["content"]
    assert out["messages"][1]["content"] == "You are an assistant."
    assert out["messages"][2]["content"] == "hi"
    assert out["messages"][3]["content"] == "prev"


def test_caller_payload_is_not_mutated():
    """The payload is shared with the affinity key and classifier."""
    p = load_proxy()
    src = payload("hi")
    before = [dict(m) for m in src["messages"]]
    p.apply_directives(src)
    assert src["messages"] == before, "caller's payload was mutated"
    assert len(src["messages"]) == 1


def test_injection_is_idempotent():
    p = load_proxy()
    once = p.apply_directives(payload("hi"))
    twice = p.apply_directives(once)
    assert once["messages"] == twice["messages"], "directives stacked"
    marker_count = sum(
        1 for m in twice["messages"] if p.DIRECTIVE_MARKER in m.get("content", "")
    )
    assert marker_count == 1


def test_non_dict_payload_is_safe():
    p = load_proxy()
    assert p.apply_directives("nope") == "nope"
    assert p.apply_directives({"model": "x"})["messages"][0]["role"] == "system"


# --- Caveman intensity --------------------------------------------------


def test_default_intensity_is_lite():
    p = load_proxy()
    assert p._caveman_intensity(payload("just a normal question")) == "lite"
    assert "lite" in p.build_directives(payload("normal question"))


def test_full_intensity_when_explicitly_requested():
    p = load_proxy()
    for text in (
        "caveman full",
        "use caveman mode ultra please",
        "/caveman",
        "go wenyan-ultra",
    ):
        assert p._caveman_intensity(payload(text)) == "full", text
        assert "FULL" in p.build_directives(payload(text))


def test_plain_caveman_word_stays_lite():
    """"caveman" alone must not escalate intensity; lite is the safe default."""
    p = load_proxy()
    assert p._caveman_intensity(payload("is caveman a real word?")) == "lite"


def test_full_is_not_sticky_across_turns():
    """One 'caveman full' turn must not fragment the whole thread."""
    p = load_proxy()
    thread = payload("please answer normally", extra=[
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "caveman full"},
    ])
    assert p._caveman_intensity(thread) == "full"

    later = payload("now explain this normally", extra=[
        {"role": "user", "content": "caveman full"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "now explain this normally"},
    ])
    assert p._caveman_intensity(later) == "lite", "intensity leaked into a later turn"


# --- Interaction with the rest of the proxy -----------------------------


def test_directives_do_not_break_conversation_key_stability():
    """Injecting a fixed prefix must not make the fingerprint drift.

    The key uses the first user turn, so an unchanged thread keeps its key
    across turns even though the payload now carries a system message.
    """
    p = load_proxy()
    first = p.apply_directives(payload("topic one"))
    grown = p.apply_directives(payload("topic one", extra=[
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "b"},
    ]))
    assert p.conversation_key(first) == p.conversation_key(grown)


def test_directives_do_not_break_task_classification():
    """The injected text must not trip the code/length heuristics."""
    p = load_proxy()
    out = p.apply_directives(payload("hi"))
    assert p.classify_request(out) == "short_chat", "directives skewed the classifier"


def test_long_context_measured_on_client_text_not_directives():
    """Directive length must not fake a long-context request."""
    p = load_proxy()
    out = p.apply_directives(payload("short question"))
    directive_len = len(p.build_directives(payload("x")))
    # The directive block alone is large, but it must not be counted.
    assert directive_len > 400, "expected a substantial directive block"
    assert p._payload_text(out)[1] == len("short question"), (
        "injected text was counted as conversation length"
    )
    assert p.classify_request(out) != "long_context"


def test_disabled_by_env(monkeypatch=None):
    p = load_proxy()
    p.DIRECTIVES_ENABLED = False
    src = payload("hi")
    assert p.apply_directives(src) is src
    assert p.build_directives(src) == ""


def test_build_directives_contains_ponytail_ladder():
    p = load_proxy()
    text = p.build_directives(payload("x"))
    assert "YAGNI" in text
    assert "root cause" in text


def test_role_alternation_is_not_broken_by_injection():
    """Two system messages in a row are tolerated by providers, but the user's
    first real turn must still be findable by the classifier."""
    p = load_proxy()
    out = p.apply_directives(payload("find me", system="client system"))
    roles = [m["role"] for m in out["messages"]]
    assert roles[0] == "system"
    assert "user" in roles
    assert roles.index("user") == 2, "user turn moved unexpectedly"
