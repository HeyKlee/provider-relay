import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import relay
import relay_config as config

@pytest.mark.parametrize('definition', config.definitions(), ids=lambda p: p['id'])
def test_each_provider_has_link_hidden_key_and_persistent_wiring(definition, capsys):
    key = 'fixture-provider-token-123456'
    base = definition['base'] or 'https://gateway.example/v1'
    # Providers with a known endpoint only ask for the key; gateways also ask for their URL.
    inputs = [] if definition['base'] else [base]
    with patch('builtins.input', side_effect=inputs), patch('getpass.getpass', return_value=key):
        assert relay.setup(definition['id']) == 0
    saved = config.load_config()['providers'][definition['id']]
    assert saved['key'] == key
    assert saved['base'] == base
    row = config.configured_providers()[definition['id']]
    assert row['key'] == key and row['base'] == base
    output = capsys.readouterr().out
    assert definition['site'] in output
    assert key not in output
    if os.name != 'nt':
        assert config.config_path().stat().st_mode & 0o777 == 0o600

def test_clickable_terminal_link_and_plain_fallback():
    terminal = io.StringIO()
    terminal.isatty = lambda: True
    with patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=True):
        link = relay.hyperlink('https://console.groq.com/keys', stream=terminal)
    assert '\x1b]8;;https://console.groq.com/keys\x1b\\' in link
    assert '\x1b' not in relay.hyperlink('https://console.groq.com/keys', stream=io.StringIO())

def test_repeat_setup_keep_disable_and_cancel(capsys):
    with patch('builtins.input', side_effect=[]), patch('getpass.getpass', return_value='fixture-keep-key'):
        relay.setup('groq')
    with patch('builtins.input', return_value=''):
        relay.setup('groq')
    assert config.configured_providers()['groq']['key'] == 'fixture-keep-key'
    with patch('builtins.input', side_effect=['r']), patch('getpass.getpass', side_effect=KeyboardInterrupt()):
        with pytest.raises(KeyboardInterrupt):
            relay.setup('groq')
    assert config.configured_providers()['groq']['key'] == 'fixture-keep-key'
    with patch('builtins.input', return_value='d'):
        relay.setup('groq')
    assert not config.configured_providers()['groq']['key']
    assert 'fixture-keep-key' not in config.config_path().read_text()

def test_anonymous_access_is_explicit():
    assert not config.configured_providers()['kilo']['anonymous']
    with patch('builtins.input', side_effect=['y']), patch('getpass.getpass', return_value=''):
        relay.setup('kilo')
    assert config.configured_providers()['kilo']['anonymous']

@pytest.mark.parametrize('url', ['https://key:secret@example.com/v1', 'https://example.com/v1?key=secret',
                                 'http://remote.example/v1', 'file:///tmp/test', 'https://example.com/\nheader'])
def test_invalid_or_unsafe_bases_are_rejected(url):
    with pytest.raises(ValueError):
        config.validate_base(url)

def test_corrupt_config_is_preserved_and_atomic_save_failure_keeps_old_data(monkeypatch):
    original = {'version': 1, 'providers': {'groq': {'enabled': True, 'base': 'https://api.groq.com/openai/v1', 'key': 'fixture-old-key'}}}
    config.save_config(original)
    with patch('os.replace', side_effect=OSError('fixture write failure')):
        with pytest.raises(OSError):
            config.save_config({'version': 1, 'providers': {}})
    assert config.load_config() == original
    assert not list(config.config_path().parent.glob('*.tmp'))
    config.config_path().write_text('{broken')
    with pytest.raises(ValueError):
        config.load_config()
    assert config.config_path().read_text() == '{broken'

def test_secrets_are_redacted_including_json_escaped_variants():
    secret = 'fixture-quote-"-token'
    config.save_config({'version': 1, 'providers': {'groq': {'enabled': True, 'base': 'https://api.groq.com/openai/v1', 'key': secret}}})
    assert secret not in config.redact('upstream echoed ' + secret)
    assert json.dumps(secret)[1:-1] not in config.redact(json.dumps({'error': secret}))

def test_paid_routes_blocked_by_default(monkeypatch):
    from test_proxy_post_paths import load_proxy
    monkeypatch.delenv('ALLOW_PAID')
    router = load_proxy()
    router.providers = lambda: {'chatgpt': {'key': 'fixture'}, 'groq': {'key': 'fixture'}}
    entry = {'steps': [{'provider': 'chatgpt', 'model': 'paid'}, {'provider': 'groq', 'model': 'free', 'paid': False}]}
    assert router._steps('Relay', entry) == [{'provider': 'groq', 'model': 'free', 'paid': False}]
    monkeypatch.setenv('ALLOW_PAID', '1')
    assert len(router._steps('Relay', entry)) == 2

def test_dahl_setup_uses_live_allowance_pool(monkeypatch):
    with patch('builtins.input', side_effect=[]), patch('getpass.getpass', return_value='fixture-dahl-key'):
        relay.setup('dahl')
    config.apply_environment()
    assert json.loads(os.environ['DAHL_API_KEYS']) == ['fixture-dahl-key']

def test_all_catalogue_providers_have_independent_setup_and_native_models():
    catalogue = json.loads((config.ROOT / 'models.json').read_text())
    provider_ids = {p['id'] for p in config.definitions()}
    for entry in catalogue.values():
        for step in entry.get('steps', [entry]):
            assert step['provider'] in provider_ids
            for model in step.get('models', [step.get('model')]):
                if model:
                    assert not model.startswith('custom:')
    assert len(provider_ids) == 19

def test_response_style_is_opt_in(monkeypatch):
    from test_proxy_post_paths import load_proxy
    monkeypatch.delenv('DIRECTIVES_ENABLED')
    router = load_proxy()
    payload = {'messages': [{'role': 'user', 'content': 'hello'}]}
    assert not router.DIRECTIVES_ENABLED
    assert router.apply_directives(payload) is payload

def test_menu_picks_by_number_or_id_and_survives_bad_input(capsys):
    numbers = {p['id']: str(i) for i, p in enumerate(config.definitions(), 1)}
    answers = ['nope', numbers['groq'], 'OpenRouter', '']
    keys = ['fixture-groq-key', 'fixture-openrouter-key']
    with patch('builtins.input', side_effect=answers), patch('getpass.getpass', side_effect=keys):
        assert relay.setup() == 0
    ready = config.configured_providers()
    assert ready['groq']['key'] == 'fixture-groq-key'
    assert ready['openrouter']['key'] == 'fixture-openrouter-key'
    output = capsys.readouterr().out
    assert 'Not a provider number or ID.' in output
    assert '2 provider(s) ready' in output
    assert 'fixture-groq-key' not in output

def test_menu_reports_errors_and_keeps_going(capsys):
    # A gateway with no URL entered is rejected without ending the session.
    with patch('builtins.input', side_effect=['9router', '', '']), patch('getpass.getpass', return_value='unused'):
        assert relay.setup() == 0
    output = capsys.readouterr().out
    assert 'Error: 9Router gateway needs the API base URL' in output
    assert 'No providers set up yet' in output

def test_url_flag_allows_custom_endpoint_for_known_provider():
    with patch('builtins.input', side_effect=['https://eu.example.com/v1']), patch('getpass.getpass', return_value='fixture-ovh'):
        relay.setup('ovh', ask_url=True)
    assert config.configured_providers()['ovh']['base'] == 'https://eu.example.com/v1'
    # Re-running without --url keeps the saved custom endpoint.
    with patch('builtins.input', side_effect=['r']), patch('getpass.getpass', return_value='fixture-ovh-2'):
        relay.setup('ovh')
    assert config.configured_providers()['ovh'] == {**config.configured_providers()['ovh'],
                                                     'base': 'https://eu.example.com/v1', 'key': 'fixture-ovh-2'}

def test_offer_to_start_only_in_an_interactive_terminal(monkeypatch):
    config.save_config({'version': 1, 'providers': {'groq': {'enabled': True, 'base': 'https://api.groq.com/openai/v1', 'key': 'fixture'}}})
    started = []
    monkeypatch.setattr(relay, 'serve', lambda host, port: started.append((host, port)) or 0)
    with patch('builtins.input', side_effect=['']):
        assert relay.setup(offer_start=True) == 0
    assert not started
    monkeypatch.setattr('sys.stdin.isatty', lambda: True, raising=False)
    with patch('builtins.input', side_effect=['', '']):
        assert relay.setup(offer_start=True) == 0
    assert started == [('127.0.0.1', 11437)]
    with patch('builtins.input', side_effect=['', 'n']):
        assert relay.setup(offer_start=True) == 0
    assert len(started) == 1
