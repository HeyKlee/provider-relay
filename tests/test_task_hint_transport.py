import io
import pytest
from test_proxy_post_paths import build,serve,post,reply

@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('task',['general','coding'])
def test_hint_selects_qualified_free_route_but_never_reaches_upstream(monkeypatch,stream,task):
    calls=[]
    def call(provider,model,payload):
        assert 'relay_task' not in payload
        assert payload['messages'][-1]['content']=='neutral fixture'
        calls.append(provider)
        return reply('qualified answer')
    p=build(monkeypatch,catalog={'Relay':{'steps':[
        {'provider':'chatgpt','model':'gpt-6-astra'},
        {'provider':'openrouter','model':'openrouter/free'},
        {'provider':'groq','model':'openai/gpt-oss-20b','paid':False},
    ]}},providers_map={key:{'key':'k'} for key in ['chatgpt','openrouter','groq']},call_impl=call)
    def streamed(provider,model,payload):
        call(provider,model,payload)
        return io.BytesIO(b'\n\ndata: [DONE]\n\n'),b'data: {"choices":[{"delta":{"content":"qualified answer"}}]}\n'
    p._stream_provider=streamed
    srv,port=serve(p)
    try:
        response=post(port,{'model':'Relay','relay_task':task,'messages':[{'role':'user','content':'neutral fixture'}],'stream':stream,'max_tokens':32})
    finally:srv.shutdown()
    assert b'200' in response.split(b'\r\n')[0]
    assert b'qualified answer' in response
    assert calls==['groq']

def test_real_image_input_overrides_explicit_general_hint():
    from test_proxy_post_paths import load_proxy
    p=load_proxy()
    assert p.classify_request({'relay_task':'general','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]}]})=='vision'
