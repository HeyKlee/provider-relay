"""Exercise saved setup and the actual server against local HTTP providers."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest
import relay
import relay_config

@contextmanager
def upstream():
    seen = []
    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            body = json.dumps({'data': [{'id': 'fixture-model'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            key = self.headers.get('Authorization')
            seen.append((key, payload))
            assert 'relay_task' not in payload
            if key == 'Bearer fixture-bad-key':
                body = json.dumps({'error': 'fixture-bad-key upstream unavailable'}).encode()
                self.send_response(503)
            elif payload.get('stream'):
                body = b'data: {"choices":[{"delta":{"content":"fixture answer"}}]}\n\ndata: [DONE]\n\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
            else:
                body = json.dumps({'model': payload['model'], 'choices': [{'message': {'role': 'assistant', 'content': 'fixture answer'}}]}).encode()
                self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

def query(port, path, payload=None, token='fixture-client-token'):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        connection.request('POST' if payload is not None else 'GET', path,
                           body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()

@contextmanager
def running(port, env):
    process = subprocess.Popen([sys.executable, str(relay_config.ROOT / 'relay.py'), 'serve', '--port', str(port)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode())
            try:
                if query(port, '/live')[0] == 200:
                    break
            except OSError:
                time.sleep(0.03)
        else:
            raise AssertionError('Server did not start')
        yield process
    finally:
        process.terminate()
        process.communicate(timeout=5)

def test_setup_check_json_stream_failover_auth_and_restart(monkeypatch, tmp_path, capsys):
    with upstream() as (base, seen):
        for identity, key in [('groq', 'fixture-bad-key'), ('nim', 'fixture-good-key')]:
            with patch('builtins.input', side_effect=['y', base]), patch('getpass.getpass', return_value=key):
                assert relay.setup(identity) == 0
        assert relay.check('nim') == 0
        catalogue = tmp_path / 'models.json'
        catalogue.write_text(json.dumps({'Relay': {'steps': [
            {'provider': 'groq', 'model': 'bad', 'paid': False},
            {'provider': 'nim', 'model': 'good', 'paid': False}]}}))
        env = dict(os.environ, MODELS_FILE=str(catalogue), HEDGE_ENABLED='0',
                   PROXY_API_KEY='fixture-client-token', OR_RETRIES='0', NIM_RETRIES='0',
                   ALLOW_PAID='0', RETRY_BACKOFF='0', PYTHONDONTWRITEBYTECODE='1')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        for restart in range(2):
            with running(port, env):
                assert query(port, '/live', token='wrong')[0] == 401
                code, data = query(port, '/v1/models')
                assert code == 200 and json.loads(data)['data'][0]['id'] == 'Relay'
                assert query(port, '/v1/chat/completions', [1, 2, 3])[0] == 400
                code, data = query(port, '/v1/chat/completions', {'model': 'Relay', 'relay_task': 'coding',
                                                               'messages': [{'role': 'user', 'content': 'hi'}]})
                assert code == 200 and json.loads(data)['choices'][0]['message']['content'] == 'fixture answer'
                code, data = query(port, '/v1/chat/completions', {'model': 'Relay', 'stream': True,
                                                               'messages': [{'role': 'user', 'content': 'stream'}]})
                assert code == 200 and b'fixture answer' in data and b'[DONE]' in data
                assert query(port, '/health')[0] == 200
            assert relay_config.configured_providers()['nim']['key'] == 'fixture-good-key'
        assert any(key == 'Bearer fixture-bad-key' for key, _ in seen)
        assert any(key == 'Bearer fixture-good-key' for key, _ in seen)
        log = Path(os.environ['PROXY_LOG']).read_text()
        assert 'fixture-bad-key' not in log
        assert 'fixture-good-key' not in log
        assert 'fixture-good-key' not in capsys.readouterr().out

def test_check_chat_uses_saved_direct_credentials_and_cost_metadata(capsys, monkeypatch):
    monkeypatch.delenv('ALLOW_PAID')
    with upstream() as (base, seen):
        with patch('builtins.input', side_effect=['y', base]), patch('getpass.getpass', return_value='fixture-good-key'):
            relay.setup('groq')
        assert relay.check('groq', chat=True) == 0
        assert seen[0][0] == 'Bearer fixture-good-key'
        assert seen[0][1]['model'] == 'openai/gpt-oss-120b'
        assert 'fixture-good-key' not in capsys.readouterr().out
