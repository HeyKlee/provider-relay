#!/usr/bin/env python3
"""Provider Relay setup and server launcher. Python 3.11+."""
import argparse
import getpass
import http.client
import json
import os
import subprocess
from pathlib import Path
import sys
import warnings
from urllib.parse import urlsplit

from relay_config import (ROOT, apply_environment, config_path, configured_providers,
                          definitions, load_config, redact, save_config, validate_base)

def hyperlink(url, label='Open provider site', stream=None):
    stream = stream or sys.stdout
    if stream.isatty() and os.environ.get('RELAY_PLAIN_LINKS') != '1':
        return f'\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\ ({url})'
    return f'{label}: {url}'

def hidden_key(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            value = getpass.getpass(prompt).strip()
        except getpass.GetPassWarning:
            raise ValueError('A terminal with hidden input is required. Run setup in Windows Terminal or a normal terminal.') from None
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('The API key must contain printable ASCII characters without spaces.')
    return value

CMD = 'python' if os.name == 'nt' else 'python3'
DEFAULT_HOST = os.environ.get('PROXY_HOST', '127.0.0.1')
DEFAULT_PORT = int(os.environ.get('PROXY_PORT', '11437'))

def select_providers(only):
    selected = [p for p in definitions() if not only or p['id'].lower() == only.lower()]
    if not selected:
        raise ValueError(f'Unknown provider. Run {CMD} relay.py providers to see valid IDs.')
    return selected

def is_ready(row):
    return bool(row.get('key') or row.get('anonymous'))

def show_menu():
    ready = configured_providers()
    rows = list(enumerate(definitions(), 1))
    for title, group in (('Hosted providers', [r for r in rows if r[1]['base']]),
                         ('Your own endpoint (self-hosted gateways)', [r for r in rows if not r[1]['base']])):
        print(f'\n{title}')
        for number, provider in group:
            if is_ready(ready[provider['id']]):
                status = 'ready'
            else:
                status = 'key optional' if provider.get('allow_anonymous') else ''
            print(f'  {number:>2}  {provider["name"]:<36} {status}')

def pick_provider(choice):
    providers = definitions()
    if choice.isdigit() and 1 <= int(choice) <= len(providers):
        return providers[int(choice) - 1]
    return next((p for p in providers if p['id'].lower() == choice.lower()), None)

def configure_provider(provider, config, ask_url=False):
    """Set up one provider: show where to get a key, then ask for it."""
    identity = provider['id']
    old = config['providers'].get(identity, {})
    print(f'\n--- {provider["name"]} ---')
    print(hyperlink(provider['site'], 'Get a key'))
    print(provider['note'])
    if old.get('enabled'):
        answer = input('Already set up. Press Enter to keep it, [r] to replace the key, [d] to disable: ').strip().lower()
        if answer in ('d', 'disable'):
            config['providers'][identity] = {'enabled': False}
            save_config(config)
            print('Disabled; saved key removed.')
            return
        if answer not in ('r', 'replace', 'u', 'update'):
            print('Kept.')
            return
    base = old.get('base') or provider['base']
    if ask_url or not base:
        prompt = f'API base URL [{base}]: ' if base else 'API base URL of your gateway (for example http://127.0.0.1:3000/v1): '
        base = input(prompt).strip() or base
        if not base:
            raise ValueError(f'{provider["name"]} needs the API base URL of your own installation.')
    else:
        print('Endpoint: ' + base)
    base = validate_base(base)
    if old.get('key'):
        hint = 'Enter keeps the saved key'
    elif provider.get('allow_anonymous'):
        hint = 'Enter to use it without a key'
    else:
        hint = 'Enter to skip'
    key = hidden_key(f'Paste API key (hidden; {hint}): ') or old.get('key', '')
    anonymous = False
    if not key:
        if not provider.get('allow_anonymous'):
            print('No key entered; nothing changed.')
            return
        anonymous = input(f'Use {provider["name"]} without a key? [y/N]: ').strip().lower() in ('y', 'yes')
        if not anonymous:
            print('Nothing changed.')
            return
    config['providers'][identity] = {'enabled': True, 'base': base, 'key': key, 'anonymous': anonymous}
    save_config(config)
    print(f'Saved {provider["name"]}.')

def setup(only=None, ask_url=False, offer_start=False):
    config = load_config()
    if only:
        configure_provider(select_providers(only)[0], config, ask_url)
    else:
        print('Provider Relay setup\n')
        print('Relay needs an API key from at least one provider. Pick one below, open its')
        print('link, create a key and paste it here. OpenRouter and Groq are quick places to start.')
        print(f'Keys are stored only on this computer: {config_path()}')
        show_menu()
        prompt = '\nType a number to set up a provider (Enter when finished): '
        while True:
            choice = input(prompt).strip()
            if choice.lower() in ('', 'q', 'quit', 'done'):
                break
            if choice.lower() in ('l', 'list'):
                show_menu()
                continue
            prompt = '\nAnother number, [l] to list providers, or Enter to finish: '
            provider = pick_provider(choice)
            if provider is None:
                print('Not a provider number or ID.')
                continue
            try:
                configure_provider(provider, config, ask_url)
            except ValueError as exc:
                print('Error: ' + str(exc))
    count = sum(is_ready(row) for row in configured_providers().values())
    if not count:
        print(f'\nNo providers set up yet. Run {CMD} relay.py again when you have a key.')
        return 0
    print(f'\n{count} provider(s) ready. Paid models stay off unless you start with --allow-paid.')
    if offer_start and sys.stdin.isatty():
        if input('Start the relay now? [Y/n]: ').strip().lower() in ('', 'y', 'yes'):
            return serve(DEFAULT_HOST, DEFAULT_PORT)
    print(f'Optional test: {CMD} relay.py check')
    print(f'Start the relay: {CMD} relay.py serve')
    return 0

def provider_list():
    configured = configured_providers()
    for provider in definitions():
        status = 'ready' if is_ready(configured[provider['id']]) else 'not set'
        print(f'{provider["id"]:12} {status:8} {provider["name"]}')
        print('  ' + hyperlink(provider['site'], 'Get a key'))
    return 0

def request(provider, method, suffix, payload=None):
    parsed = urlsplit(validate_base(provider['base']))
    connection_type = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=20)
    headers = {'Accept': 'application/json', 'User-Agent': 'ProviderRelay/0.1'}
    if provider.get('key'):
        headers['Authorization'] = 'Bearer ' + provider['key']
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    try:
        connection.request(method, parsed.path.rstrip('/') + suffix,
                           body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        response = connection.getresponse()
        # No redirects: bearer credentials are never forwarded to another host.
        data = response.read(4 * 1024 * 1024 + 1)
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}; check endpoint, credentials and account quota')
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('Provider response exceeds 4 MiB')
        return json.loads(data)
    finally:
        connection.close()

def check(only=None, chat=False):
    providers = configured_providers()
    failures, tested = 0, 0
    for definition in select_providers(only):
        identity = definition['id']
        provider = providers[identity]
        if not provider.get('key') and not provider.get('anonymous'):
            print(f'{identity}: SKIP (disabled)')
            continue
        tested += 1
        try:
            if chat:
                apply_environment()
                import router
                model = provider['model']
                step = {'provider': identity, 'model': model}
                for entry in router.load_catalog().values():
                    if entry.get('provider') == identity and entry.get('model') == model and 'paid' in entry:
                        step['paid'] = entry['paid']
                        break
                if os.environ.get('ALLOW_PAID', '0') != '1' and router._paid_step(step):
                    raise ValueError('Paid route is blocked. Use --allow-paid only if you approve provider charges.')
                payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with the word OK.'}], 'max_tokens': 32, 'stream': False}
                response = router._call_provider(identity, model, payload)
                if not router._acceptable_response(response, payload):
                    raise ValueError('No usable assistant answer returned')
                print(f'{identity}: PASS (chat response received)')
            else:
                response = request(provider, 'GET', '/models')
                if not isinstance(response, dict) or not isinstance(response.get('data'), list):
                    raise ValueError('Expected an OpenAI-compatible model list')
                print(f'{identity}: PASS (catalogue access, {len(response["data"])} models; inference untested)')
        except (OSError, http.client.HTTPException, ValueError) as exc:
            failures += 1
            print(f'{identity}: FAIL ({redact(str(exc))})')
    if not tested:
        print(f'No providers configured. Run {CMD} relay.py to set one up.')
        return 1
    return 1 if failures else 0

def serve(host, port):
    if host not in ('localhost', '127.0.0.1', '::1') and not os.environ.get('PROXY_API_KEY'):
        raise ValueError('Set PROXY_API_KEY before listening beyond loopback.')
    apply_environment()
    providers = configured_providers()
    if not any(p.get('key') or p.get('anonymous') for p in providers.values()):
        raise ValueError(f'No providers configured. Run {CMD} relay.py to set one up.')
    os.environ['PROXY_HOST'], os.environ['PROXY_PORT'] = host, str(port)
    import router
    catalogue = router.load_catalog()
    if not any(router._steps(alias, entry) for alias, entry in catalogue.items()):
        raise ValueError('No eligible routes. Check models.json, provider setup and paid-route opt-in.')
    if not router._steps('Relay', catalogue.get('Relay', {})):
        print('Automatic routing has no eligible provider. Choose an individual model alias from /v1/models.', flush=True)
    router._load_budget()
    router._start_dahl_monitor()
    client_key = 'your PROXY_API_KEY' if os.environ.get('PROXY_API_KEY') else 'any placeholder, e.g. "relay"'
    print('Provider Relay is running. Point your app at:', flush=True)
    print(f'  Base URL  http://{host}:{port}/v1', flush=True)
    print('  Model     Relay (automatic), or any alias listed at /v1/models', flush=True)
    print(f'  API key   {client_key}', flush=True)
    print('Press Ctrl+C to stop.', flush=True)
    try:
        with router.ThreadingHTTPServer((host, port), router.Handler) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        router._dahl_monitor_stop.set()
    return 0

def main():
    parser = argparse.ArgumentParser(description='Provider Relay: bring your own provider keys.')
    parser.add_argument('--config', help='Private configuration path (default: .provider-relay/config.json)')
    sub = parser.add_subparsers(dest='command')
    wizard = sub.add_parser('setup', help='Add, change or remove provider keys (default command)')
    wizard.add_argument('--only', help='Set up a single provider by ID (see: providers)')
    wizard.add_argument('--url', action='store_true', help='Also ask for the API base URL (custom or regional endpoints)')
    sub.add_parser('providers', help='List providers and which are ready (never shows keys)')
    probe = sub.add_parser('check', help='Test that configured providers respond')
    probe.add_argument('--only', help='Check a single provider by ID')
    probe.add_argument('--chat', action='store_true', help='Send a small inference request; may consume quota')
    probe.add_argument('--allow-paid', action='store_true', help='Permit paid inference in this command')
    server = sub.add_parser('serve', help='Start the OpenAI-compatible API (local only by default)')
    server.add_argument('--host', default=DEFAULT_HOST)
    server.add_argument('--port', type=int, default=DEFAULT_PORT)
    server.add_argument('--allow-paid', action='store_true', help='Permit fallback to paid routes')
    args = parser.parse_args()
    if args.config:
        os.environ['RELAY_CONFIG'] = str(Path(args.config).resolve())
    if getattr(args, 'allow_paid', False):
        os.environ['ALLOW_PAID'] = '1'
    try:
        if args.command in (None, 'setup'):
            only = getattr(args, 'only', None)
            return setup(only, getattr(args, 'url', False), offer_start=not only)
        if args.command == 'providers':
            return provider_list()
        if args.command == 'check':
            return check(args.only, args.chat)
        return serve(args.host, args.port)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup stopped. Providers already saved are retained.', file=sys.stderr)
        return 130

if __name__ == '__main__':
    sys.exit(main())
