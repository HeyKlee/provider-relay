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

def select_providers(only):
    selected = [p for p in definitions() if not only or p['id'].lower() == only.lower()]
    if not selected:
        raise ValueError('Unknown provider. Run python relay.py providers for valid IDs.')
    return selected

def setup(only=None):
    config = load_config()
    print('Provider Relay setup\n')
    print('Choose providers to configure. Keys are hidden and stored only in the private local config.')
    print('Each saved provider can be kept, updated or disabled. No requests are sent during setup.')
    for provider in select_providers(only):
        identity = provider['id']
        old = config['providers'].get(identity, {})
        print(f'\n{provider["name"]} [{identity}]')
        print(hyperlink(provider['site']))
        print(provider['note'])
        prompt = 'Update, keep or disable? [u/K/d]: ' if old.get('enabled') else 'Configure this provider? [y/N]: '
        answer = input(prompt).strip().lower()
        if old.get('enabled') and answer == 'd':
            config['providers'][identity] = {'enabled': False}
            save_config(config)
            print('Disabled; saved key removed.')
            continue
        if answer not in (('u', 'update') if old.get('enabled') else ('y', 'yes')):
            print('Kept.' if old.get('enabled') else 'Skipped.')
            continue
        default_base = old.get('base') or provider['base']
        base = input(f'API base URL [{default_base or "required"}]: ').strip() or default_base
        base = validate_base(base)
        key = hidden_key('Paste API key (hidden, Enter to keep saved key or choose anonymous): ')
        key = key or old.get('key', '')
        anonymous = False
        if not key:
            if not provider.get('allow_anonymous'):
                print('No key entered; previous configuration kept.')
                continue
            anonymous = input('Enable this endpoint without a key? [y/N]: ').strip().lower() in ('y', 'yes')
            if not anonymous:
                print('Previous configuration kept.')
                continue
        config['providers'][identity] = {'enabled': True, 'base': base, 'key': key, 'anonymous': anonymous}
        save_config(config)
        print('Saved locally.')
    count = sum(bool(v.get('enabled')) for v in config['providers'].values())
    print(f'\n{count} provider(s) enabled. Paid routes are disabled by default.')
    print('Next: python relay.py check\nStart: python relay.py serve')
    return 0

def provider_list():
    configured = configured_providers()
    for provider in definitions():
        row = configured[provider['id']]
        status = 'configured' if row.get('key') or row.get('anonymous') else 'disabled'
        print(f'{provider["id"]:12} {status:10} {provider["name"]}')
        print('  ' + hyperlink(provider['site']))
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
        print('No providers configured. Run python relay.py setup.')
        return 1
    return 1 if failures else 0

def serve(host, port):
    if host not in ('localhost', '127.0.0.1', '::1') and not os.environ.get('PROXY_API_KEY'):
        raise ValueError('Set PROXY_API_KEY before listening beyond loopback.')
    apply_environment()
    providers = configured_providers()
    if not any(p.get('key') or p.get('anonymous') for p in providers.values()):
        raise ValueError('No providers configured. Run python relay.py setup.')
    os.environ['PROXY_HOST'], os.environ['PROXY_PORT'] = host, str(port)
    import router
    catalogue = router.load_catalog()
    if not any(router._steps(alias, entry) for alias, entry in catalogue.items()):
        raise ValueError('No eligible routes. Check models.json, provider setup and paid-route opt-in.')
    if not router._steps('Relay', catalogue.get('Relay', {})):
        print('Automatic routing has no eligible provider. Choose an individual model alias from /v1/models.', flush=True)
    router._load_budget()
    router._start_dahl_monitor()
    print(f'Provider Relay listening at http://{host}:{port}/v1 (model: Relay)', flush=True)
    print('Ctrl+C stops the server. /live reports process liveness; /health reports observed upstream results.', flush=True)
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
    wizard = sub.add_parser('setup', help='Guided provider setup with terminal hyperlinks and hidden keys')
    wizard.add_argument('--only', help='Configure one provider ID')
    sub.add_parser('providers', help='List all providers without displaying keys')
    probe = sub.add_parser('check', help='Check catalogue access, or explicitly test inference')
    probe.add_argument('--only')
    probe.add_argument('--chat', action='store_true', help='Send a small inference request; may consume quota')
    probe.add_argument('--allow-paid', action='store_true', help='Permit paid inference in this command')
    server = sub.add_parser('serve', help='Start the compatible API on loopback')
    server.add_argument('--host', default=os.environ.get('PROXY_HOST', '127.0.0.1'))
    server.add_argument('--port', type=int, default=int(os.environ.get('PROXY_PORT', '11437')))
    server.add_argument('--allow-paid', action='store_true', help='Permit fallback to paid routes')
    args = parser.parse_args()
    if args.config:
        os.environ['RELAY_CONFIG'] = str(Path(args.config).resolve())
    if getattr(args, 'allow_paid', False):
        os.environ['ALLOW_PAID'] = '1'
    try:
        if args.command in (None, 'setup'):
            return setup(getattr(args, 'only', None))
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
