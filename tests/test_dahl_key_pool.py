import io,json,urllib.error
import pytest
from test_proxy_post_paths import load_proxy

@pytest.fixture
def pool(monkeypatch,tmp_path):
    monkeypatch.setenv('DAHL_API_KEYS',json.dumps(['key-a','key-b','key-c']))
    monkeypatch.setenv('DAHL_POOL_FILE',str(tmp_path/'pool.json'))
    p=load_proxy();p._dahl_pool={'cursor':0,'keys':{}};p._log=lambda line:None
    return p

def reply():
    return {'choices':[{'message':{'content':'OK'}}],'usage':{'total_tokens':20}}

def payload():return {'messages':[],'max_tokens':16}

def test_round_robin_checks_balances_and_accounts_each_key(pool):
    calls=[];balances=[]
    pool._dahl_query_balance=lambda key:balances.append(key) or 1000000
    pool._call_provider_once=lambda p,m,b,_dahl_key: calls.append(_dahl_key) or reply()
    for _ in range(6):pool._call_provider('dahl','model',payload())
    assert calls==['key-a','key-b','key-c']*2
    assert balances==[k for k in calls for _ in range(2)]
    assert [r['observed_usage'] for r in pool.budget_state()['keys']]==[40,40,40]
    assert pool.budget_state()['remaining_tokens']==3000000

def test_empty_invalid_or_unchecked_keys_never_receive_inference(pool):
    def balance(key):
        if key=='key-a':return 0
        if key=='key-b':raise urllib.error.HTTPError('https://example',401,'invalid',{},None)
        raise OSError('unavailable')
    pool._dahl_query_balance=balance
    pool._call_provider_once=lambda *a,**k:pytest.fail('unfunded key used')
    with pytest.raises(RuntimeError,match='verified sufficient allowance'):pool._call_provider('dahl','model',payload())
    assert [r['status'] for r in pool.budget_state()['keys']]==['exhausted','invalid','unverified']

def test_upstream_exhaustion_switches_key_within_same_request(pool):
    calls=[];pool._dahl_query_balance=lambda k:1000000
    def call(p,m,b,_dahl_key):
        calls.append(_dahl_key)
        if _dahl_key=='key-a':raise RuntimeError('dahl: HTTP 402 credits exhausted')
        return reply()
    pool._call_provider_once=call
    assert pool._call_provider('dahl','model',payload())['choices'][0]['message']['content']=='OK'
    assert calls==['key-a','key-b']
    assert pool.budget_state()['keys'][0]['status']=='exhausted'

def test_stream_refreshes_authoritative_balance_and_tracks_usage(pool):
    checks=[];pool._dahl_query_balance=lambda k:checks.append(k) or 1000000
    preface=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
    body=b'data: {"usage":{"total_tokens":20},"choices":[]}\n\ndata: [DONE]\n\n'
    pool._stream_provider_once=lambda p,m,b,_dahl_key:(io.BytesIO(body),preface)
    response,head=pool._stream_provider('dahl','model',payload());out=head
    while True:
        line=response.readline()
        if not line:break
        out+=line
    response.close();response.close()
    assert out==preface+body
    assert checks==['key-a','key-a']
    assert pool.budget_state()['keys'][0]['observed_usage']==20
    assert pool.budget_state()['keys'][0]['requests']==1

def test_checker_persists_and_never_contains_secrets(pool):
    pool._dahl_query_balance=lambda k:99999
    pool._dahl_refresh('key-a')
    data=json.loads(open(pool.DAHL_POOL_FILE).read())
    assert data['keys'][pool._dahl_id('key-a')]['warned']
    assert all(key not in json.dumps(data) for key in ['key-a','key-b','key-c'])
    restored=load_proxy()
    assert restored.budget_state()['keys'][0]['remaining_tokens']==99999

def test_insufficient_balance_skips_key_before_request(pool):
    calls=[];pool._dahl_query_balance=lambda k:10 if k=='key-a' else 1000000
    pool._call_provider_once=lambda p,m,b,_dahl_key:calls.append(_dahl_key) or reply()
    pool._call_provider('dahl','model',payload())
    assert calls==['key-b']



def test_replacement_alert_is_persistent_and_clears_after_refunding(pool):
    pool._dahl_query_balance=lambda k:0
    first=pool._dahl_refresh('key-a');second=pool._dahl_refresh('key-a')
    assert first['alerted_at']==second['alerted_at']
    assert pool.budget_state()['alerts'][0]['event']=='dahl_key_needs_replacement'
    restored=load_proxy()
    assert restored.budget_state()['alerts']==pool.budget_state()['alerts']
    pool._dahl_query_balance=lambda k:1000000
    pool._dahl_refresh('key-a')
    assert pool.budget_state()['alerts']==[]



def test_internal_monitor_detects_unusable_keys_without_inference(pool):
    checks = []
    def balance(key):
        checks.append(key)
        if key == 'key-a':
            return 0
        if key == 'key-b':
            raise urllib.error.HTTPError('https://example', 401, 'invalid', {}, None)
        return 1000000
    pool._dahl_query_balance = balance
    pool._dahl_monitor_cycle()
    state = pool.budget_state()
    assert checks == ['key-a', 'key-b', 'key-c']
    assert [r['status'] for r in state['keys']] == ['exhausted', 'invalid', 'available']
    assert len(state['alerts']) == 2
    assert state['monitor']['last_cycle_at'] is not None
    pool._dahl_query_balance = lambda key: 1000000
    pool._dahl_monitor_cycle()
    assert pool.budget_state()['alerts'] == []


def test_internal_monitor_starts_once_and_stops_cleanly(pool):
    import threading
    scanned = threading.Event()
    checks = []
    pool.DAHL_CHECK_INTERVAL = 0.02
    def balance(key):
        checks.append(key)
        if len(checks) >= 6:
            scanned.set()
        return 1000000
    pool._dahl_query_balance = balance
    pool._start_dahl_monitor()
    first = pool._dahl_monitor_thread
    try:
        assert scanned.wait(2)
        pool._start_dahl_monitor()
        assert pool._dahl_monitor_thread is first
        assert pool.budget_state()['monitor']['running']
    finally:
        pool._dahl_monitor_stop.set()
        first.join(2)
    assert not first.is_alive()
