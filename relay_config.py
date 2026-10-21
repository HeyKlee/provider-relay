"""Provider metadata and private, local credentials. Standard library only."""
from pathlib import Path
from urllib.parse import urlsplit
import csv
import io
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent

def definitions():
    return json.loads((ROOT / 'providers.json').read_text(encoding='utf-8'))

def config_path():
    return Path(os.environ.get('RELAY_CONFIG', str(ROOT / '.provider-relay/config.json'))).resolve()

def load_config():
    path = config_path()
    if not path.exists():
        return {'version': 1, 'providers': {}}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Cannot read private config; repair or restore it before continuing.') from exc
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('providers'), dict):
        raise ValueError('Unsupported private config format.')
    for key, row in value['providers'].items():
        if not isinstance(key, str) or not isinstance(row, dict):
            raise ValueError('Invalid provider configuration.')
        if not isinstance(row.get('key', ''), str) or not isinstance(row.get('enabled', False), bool):
            raise ValueError('Invalid provider credentials.')
        if row.get('enabled'):
            validate_base(row.get('base', ''))
    return value

def validate_base(base):
    if not isinstance(base, str) or any(ord(c) < 33 for c in base):
        raise ValueError('Enter a valid API base URL.')
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError:
        raise ValueError('Enter a valid API base URL.') from None
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use an http(s) base URL without embedded credentials, query or fragment.')
    if port is not None and not 0 < port < 65536:
        raise ValueError('Invalid endpoint port.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Use HTTPS for remote endpoints. HTTP is allowed only on loopback.')
    return base.rstrip('/')

def protect_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('The credentials directory must not be a symbolic link.')
    if os.name == 'nt':
        identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True, capture_output=True, text=True)
        sid = next(csv.reader(io.StringIO(identity.stdout.strip())))[1]
        # Protect the directory before any key is written; child files inherit it.
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True, capture_output=True)
    else:
        path.chmod(0o700)

def save_config(value):
    path = config_path()
    protect_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix='.config-', suffix='.tmp', dir=path.parent)
    try:
        if os.name != 'nt':
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def configured_providers():
    saved = load_config()['providers']
    result = {}
    for provider in definitions():
        identity = provider['id']
        row = saved.get(identity, {})
        key = os.environ.get(provider['key_env'], row.get('key', ''))
        base = os.environ.get(provider['base_env'], row.get('base', provider['base']))
        enabled = row.get('enabled', False) or bool(key)
        if row.get('enabled') is False and identity in saved:
            enabled = False
        if enabled:
            base = validate_base(base)
        result[identity] = {'base': base, 'key': key if enabled else '',
                            'anonymous': bool(enabled and row.get('anonymous') and provider.get('allow_anonymous')),
                            'model': row.get('model', provider['model'])}
    return result

def apply_environment():
    path = config_path()
    protect_directory(path.parent)
    os.environ.setdefault('PROXY_LOG', str(path.parent / 'router.log'))
    os.environ.setdefault('DAHL_POOL_FILE', str(path.parent / 'dahl-key-pool.json'))
    os.environ.setdefault('DAHL_STATE_FILE', str(path.parent / 'dahl-usage.json'))
    dahl = configured_providers()['dahl']
    if dahl.get('key') and 'DAHL_API_KEYS' not in os.environ:
        os.environ['DAHL_API_KEYS'] = json.dumps([dahl['key']])

def redact(text):
    # Sanitise error bodies as well as logs; some upstreams echo bearer tokens.
    secrets = [row.get('key', '') for row in load_config()['providers'].values()]
    secrets.extend(os.environ.get(p['key_env'], '') for p in definitions())
    secrets.append(os.environ.get('PROXY_API_KEY', ''))
    try:
        secrets.extend(json.loads(os.environ.get('DAHL_API_KEYS', '[]')))
    except ValueError:
        pass
    for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
        text = text.replace(secret, '[redacted]').replace(json.dumps(secret)[1:-1], '[redacted]')
    return text
