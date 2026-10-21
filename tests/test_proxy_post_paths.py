"""Regression: every non-streaming path must bind its locals before use.

`do_POST` is a single long function, so a rewrite of the hedge branch can drop
an initialisation that a *sibling* branch depends on. The unit tests for
hedged_call() all passed while the live pinned path returned a zero-byte
response, because nothing exercised do_POST itself.

These tests drive the real handler over a socket, so a skipped branch that
leaves a local unbound is caught here rather than in production.
"""

import importlib.util
import json
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parents[1] / "router.py"

_module_counter = [0]


def load_proxy(monkeypatched=None):
    _module_counter[0] += 1
    name = f"relay_proxy_post_{_module_counter[0]}"
    spec = importlib.util.spec_from_file_location(name, PROXY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def reply(text="OK"):
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}


def post(proxy_port, body, timeout=20):
    """Raw HTTP POST so a zero-byte / statusless failure is visible."""
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout)
    data = json.dumps(body).encode()
    s.sendall(
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(data)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + data
    )
    chunks = []
    while True:
        part = s.recv(65536)
        if not part:
            break
        chunks.append(part)
    s.close()
    return b"".join(chunks)


def serve(mod, handler_cls=None):
    handler = handler_cls or mod.Handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def build(monkeypatch, *, catalog, providers_map, call_impl):
    """A proxy module wired to fakes, serving a given catalog."""
    p = load_proxy()
    p.load_catalog = lambda: catalog
    p.providers = lambda: providers_map
    p._call_provider = call_impl
    p._stream_provider = lambda prov, model, pl: (_ for _ in ()).throw(
        RuntimeError("stream not used")
    )
    p._telemetry.clear()
    p._breaker.clear()
    p._affinity.clear()
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("HEDGE_ENABLED", "1")
    monkeypatch.setenv("HEDGE_AFTER_SECONDS", "0.2")
    yield


def test_pinned_request_returns_a_real_response(monkeypatch):
    """The exact production failure: turn 2 of a pinned thread."""
    p = build(
        monkeypatch,
        catalog={"Relay": {"steps": [
            {"provider": "p1", "model": "m1"},
            {"provider": "p2", "model": "m2"},
        ]}},
        providers_map={"p1": {"key": "k"}, "p2": {"key": "k"}},
        call_impl=lambda prov, model, pl: reply(f"from {prov}"),
    )
    body = {"model": "Relay", "messages": [
        {"role": "system", "content": "SYS-PIN"},
        {"role": "user", "content": "OPENING-PIN"},
    ], "max_tokens": 100}

    srv, port = serve(p)
    try:
        first = post(port, body)
        assert b"200" in first.split(b"\r\n")[0], first[:120]
        # Turn 2 of the SAME conversation is now pinned.
        second = post(port, body)
    finally:
        srv.shutdown()

    assert b"200" in second.split(b"\r\n")[0], f"pinned turn returned: {second[:160]!r}"
    payload = json.loads(second.split(b"\r\n\r\n", 1)[1])
    assert payload["pinned"] is True
    assert payload["hedged"] is False, "a pinned thread must not hedge"
    assert payload["choices"][0]["message"]["content"]


def test_single_tier_request_returns_a_real_response(monkeypatch):
    """A one-step chain skips the hedge branch entirely."""
    p = build(
        monkeypatch,
        catalog={"SOLO": {"provider": "p1", "model": "m1"}},
        providers_map={"p1": {"key": "k"}},
        call_impl=lambda prov, model, pl: reply("solo answer"),
    )
    body = {"model": "SOLO", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50}

    srv, port = serve(p)
    try:
        raw = post(port, body)
    finally:
        srv.shutdown()

    assert b"200" in raw.split(b"\r\n")[0], f"single-tier returned: {raw[:160]!r}"
    payload = json.loads(raw.split(b"\r\n\r\n", 1)[1])
    assert payload["choices"][0]["message"]["content"] == "solo answer"


def test_unpinned_multi_tier_request_returns_a_real_response(monkeypatch):
    p = build(
        monkeypatch,
        catalog={"Relay": {"steps": [
            {"provider": "p1", "model": "m1"},
            {"provider": "p2", "model": "m2"},
        ]}},
        providers_map={"p1": {"key": "k"}, "p2": {"key": "k"}},
        call_impl=lambda prov, model, pl: reply(f"from {prov}"),
    )
    body = {"model": "Relay", "messages": [
        {"role": "user", "content": "hi there"}], "max_tokens": 50}

    srv, port = serve(p)
    try:
        raw = post(port, body)
    finally:
        srv.shutdown()

    assert b"200" in raw.split(b"\r\n")[0]
    payload = json.loads(raw.split(b"\r\n\r\n", 1)[1])
    assert payload["pinned"] is False


def test_all_tiers_failing_returns_503_not_an_empty_body(monkeypatch):
    """A failure path must still produce a status line."""
    def boom(prov, model, pl):
        raise RuntimeError(f"{prov} down")

    p = build(
        monkeypatch,
        catalog={"Relay": {"steps": [
            {"provider": "p1", "model": "m1"},
            {"provider": "p2", "model": "m2"},
        ]}},
        providers_map={"p1": {"key": "k"}, "p2": {"key": "k"}},
        call_impl=boom,
    )
    body = {"model": "Relay", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50}

    srv, port = serve(p)
    try:
        raw = post(port, body)
    finally:
        srv.shutdown()

    assert raw, "empty response body on a total failure"
    assert b"503" in raw.split(b"\r\n")[0], raw[:160]
