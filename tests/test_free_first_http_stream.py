import io
import json
import pytest
from test_proxy_post_paths import load_proxy,build,serve,post,reply

@pytest.mark.parametrize('stream',[False,True])
def test_real_http_handler_keeps_paid_at_tail(monkeypatch,stream):
    calls=[]
    def call(provider,model,payload):
        calls.append(provider)
        return reply('free answer')
    p=build(monkeypatch,catalog={'Relay':{'steps':[
        {'provider':'chatgpt','model':'gpt-6-astra'},
        {'provider':'nim','model':'meta/llama-3.2-11b-vision-instruct'},
    ]}},providers_map={'chatgpt':{'key':'k'},'nim':{'key':'k'}},call_impl=call)
    def streamed(provider,model,payload):
        calls.append(provider)
        return io.BytesIO(b'\n\ndata: [DONE]\n\n'), b'data: {"choices":[{"delta":{"content":"free answer"}}]}\n'
    p._stream_provider=streamed
    srv,port=serve(p)
    try: raw=post(port,{'model':'Relay','messages':[{'role':'user','content':'hi'}],'stream':stream,'max_tokens':32})
    finally: srv.shutdown()
    assert b'200' in raw.split(b'\r\n')[0],raw[:200]
    assert b'free answer' in raw
    assert calls==['nim']

@pytest.mark.parametrize('content,ok',[(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: [DONE]\n\n',False),
    (b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n',True),
    (b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"terminal","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n',True),
    (b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"term"}}]}}]}\n\ndata: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"inal","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n',True),
    (b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"undeclared"}}]}}]}\n\ndata: [DONE]\n\n',False)])
def test_native_stream_preface_rejects_blank_and_preserves_bytes(monkeypatch,content,ok):
    p=load_proxy();response=io.BytesIO(content);response.status=200
    class Conn:
        def request(self,*a,**k):pass
        def getresponse(self):return response
        def close(self):pass
    p.providers=lambda:{'test':{'key':'k'}}
    p._conn_for=lambda *a:(Conn(),'/v1/chat/completions')
    p._take_conn=lambda *a:None
    p._drop_conn=lambda *a:None
    if ok:
        stream,preface=p._stream_provider_once('test','model',{'tools':[{'type':'function','function':{'name':'terminal'}}]})
        assert preface+stream.read()==content
    else:
        with pytest.raises(RuntimeError,match='without content or tools|undeclared tool'):
            p._stream_provider_once('test','model',{'tools':[{'type':'function','function':{'name':'terminal'}}]})

