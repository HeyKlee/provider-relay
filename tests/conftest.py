"""Keep router tests isolated from developer accounts and persistent state."""
from pathlib import Path
import os
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    from relay_config import definitions
    for provider in definitions():
        monkeypatch.delenv(provider['key_env'], raising=False)
        monkeypatch.delenv(provider['base_env'], raising=False)
    monkeypatch.delenv('DAHL_API_KEYS', raising=False)
    monkeypatch.delenv('PROXY_API_KEY', raising=False)
    monkeypatch.setenv('RELAY_CONFIG', str(tmp_path / 'private/config.json'))
    monkeypatch.setenv('DAHL_POOL_FILE', str(tmp_path / 'pool.json'))
    monkeypatch.setenv('DAHL_STATE_FILE', str(tmp_path / 'usage.json'))
    monkeypatch.setenv('PROXY_LOG', str(tmp_path / 'router.log'))
    # Preserve legacy routing tests, including paid-last fallback. New tests
    # explicitly remove this override to exercise the shipping default.
    monkeypatch.setenv('ALLOW_PAID', '1')
    monkeypatch.setenv('DIRECTIVES_ENABLED', '1')
