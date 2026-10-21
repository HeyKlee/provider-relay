import importlib.util
import json
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "router.py"

def load():
    spec = importlib.util.spec_from_file_location('free_first_blocks', SOURCE)
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    p._log = lambda text: None
    return p

def answer(text='OK'):
    return {'choices': [{'message': {'role': 'assistant', 'content': text}}]}

def test_paid_task_matches_never_overtake_free_routes():
    p = load()
    chain = [
        {'provider': 'chatgpt', 'model': 'gpt-6-astra'},
        {'provider': 'openrouter', 'model': 'deepseek/deepseek-v4-flash'},
        {'provider': 'xkiro', 'model': 'qwen/qwen3-coder-plus:free'},
        {'provider': 'nim', 'model': 'meta/llama-3.2-11b-vision-instruct'},
        {'provider': 'other', 'model': 'subscription', 'paid': True},
    ]
    for content in ['write a Python function', 'x' * 25000, [{'type': 'image_url', 'image_url': {'url': 'data:x'}}]]:
        steps, _ = p.promote_for_task(chain, {'messages': [{'role': 'user', 'content': content}]})
        steps = p._free_first(steps)
        assert [p._paid_step(s) for s in steps] == [False, False, True, True, True]
        assert len(steps) == len(chain)

def test_paid_affinity_cannot_overtake_free_routes():
    p = load()
    steps = [{'provider': 'xkiro', 'model': 'free'}, {'provider': 'chatgpt', 'model': 'paid'}]
    p.affinity_set('thread', 'chatgpt:paid')
    pinned, _ = p.pin_steps(steps, 'thread')
    assert p._free_first(pinned) == steps

def test_hedge_does_not_launch_paid_while_free_is_pending():
    p = load()
    calls = []
    def call(provider, model, payload):
        calls.append(provider)
        if provider == 'xkiro':
            time.sleep(0.08)
        return answer(provider)
    p._call_provider = call
    result, _, _, _ = p.hedged_call([
        {'provider': 'xkiro', 'model': 'free'}, {'provider': 'chatgpt', 'model': 'paid'}
    ], {}, delay=0.01)
    assert result['choices'][0]['message']['content'] == 'xkiro'
    assert calls == ['xkiro']

def test_paid_tail_runs_only_after_free_failure():
    p = load()
    calls = []
    def call(provider, model, payload):
        calls.append(provider)
        if provider == 'xkiro':
            raise RuntimeError('quota unavailable')
        return answer()
    p._call_provider = call
    p.hedged_call([{'provider': 'xkiro', 'model': 'free'}, {'provider': 'chatgpt', 'model': 'paid'}], {}, delay=0)
    assert calls == ['xkiro', 'chatgpt']

def test_tool_calls_are_valid_hedge_answers():
    p = load()
    assert p._acceptable_response({'choices': [{'message': {'content': None, 'tool_calls': [{'id': 'call1'}]}}]})
    assert not p._acceptable_response(answer(''))

def test_codiv_blocks_preserve_tools_recent_pairs_and_bound_every_call():
    p = load()
    calls = []
    def call(provider, model, payload):
        calls.append(payload)
        assert p._codiv_units(payload) + payload['max_tokens'] + 256 <= p.CODIV_NATIVE_CONTEXT
        return answer('Block facts: alpha=17; omega=23. Preserve exact IDs.')
    p._call_provider_once = call
    tools = [{'type': 'function', 'function': {'name': 'terminal', 'parameters': {'type': 'object'}}}]
    messages = [
        {'role': 'system', 'content': 'Do not invent facts.'},
        {'role': 'user', 'content': 'alpha=17\n' + ('filler\n' * 9000) + '\nomega=23'},
        {'role': 'user', 'content': 'What is alpha plus omega?'},
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'exact-call', 'type': 'function', 'function': {'name': 'terminal', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'exact-call', 'content': '40'},
    ]
    result = p._call_provider('codiv', 'diffusiongemma-26b', {'messages': messages, 'tools': tools, 'max_tokens': 512})
    assert result['relay_context']['blocks'] > 1
    final = calls[-1]
    assert final['tools'] == tools
    assert final['messages'][-2:] == messages[-2:]
    assert final['messages'][0] == messages[0]
    assert 'omega=23' in json.dumps(calls[:-1])
    assert 'alpha=17' in json.dumps(calls[:-1])
    assert result['relay_context']['lossless'] is False

def test_utf8_blocks_are_lossless_before_model_processing():
    p = load()
    original = 'Maori words: whenua; emoji: ' + '\U0001f680' * 12000
    blocks = list(p._utf8_blocks(original))
    assert ''.join(blocks) == original
    assert all(len(block.encode('utf-8')) <= 16384 for block in blocks)


def test_generic_router_safety_model_cannot_win_a_chat():
    p = load()
    result = answer('SAFE')
    result['model'] = 'nvidia/nemotron-3.5-content-safety:free'
    assert not p._acceptable_response(result)
    result['model'] = 'nvidia/nemotron-3-super-120b-a12b:free'
    assert p._acceptable_response(result)



def test_blank_reasoning_and_undeclared_tools_cannot_win_a_route():
    p=load()
    assert not p._acceptable_response({'choices':[{'message':{'content':None,'reasoning':'thinking'}}]}, {})
    obj={'choices':[{'message':{'content':None,'tool_calls':[{'id':'c','type':'function','function':{'name':'terminal','arguments':'{}'}}]}}]}
    assert not p._acceptable_response(obj,{})
    assert p._acceptable_response(obj,{'tools':[{'type':'function','function':{'name':'terminal'}}]})
    assert not p._acceptable_response(obj,{'tool_choice':'none','tools':[{'type':'function','function':{'name':'terminal'}}]})


def test_task_promotion_notice_does_not_claim_untried_routes_failed():
    p=load()
    chain=[{'provider':'openrouter','model':'openrouter/free'},{'provider':'openrouter','model':'cohere/north-mini-code:free'}]
    notice=p._notice_for('Relay',chain,1,task_label='code')
    assert 'code task' in notice
    assert 'unavailable' not in notice and 'recovers' not in notice
