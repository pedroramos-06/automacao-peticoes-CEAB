import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from ceab.discovery import find_existing_browsers, inspect_browser, running_browser_urls


@pytest.fixture
def cdp_server():
    targets=[{'type':'page','title':'eproc · Lista de Processos por Localizador','url':'https://eproc1g.trf6.jus.br/eproc/controlador.php?key=nao-expor'},
             {'type':'page','title':'Outra página','url':'https://example.com'},
             {'type':'iframe','title':'eproc','url':'https://eproc1g.trf6.jus.br'}]
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            requests.append(self.path)
            data={'Browser':'Brave/Chromium','webSocketDebuggerUrl':'ws://127.0.0.1/session'} if self.path=='/json/version' else targets
            body=json.dumps(data).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}',targets,requests
    server.shutdown()
    server.server_close()


def test_discovers_existing_tab_without_navigation_or_session_details(cdp_server):
    url,targets,requests=cdp_server
    result=find_existing_browsers([url])
    assert len(result)==1
    assert result[0]['endpoint']==url
    assert result[0]['tabs']==[{'title':targets[0]['title'],'queue':True}]
    assert requests==['/json/version','/json/list']
    assert 'nao-expor' not in json.dumps(result)
    assert 'webSocketDebuggerUrl' not in json.dumps(result)


def test_no_eproc_tab_is_not_an_existing_session(cdp_server):
    url,targets,_=cdp_server
    targets[:]=[{'type':'page','title':'Página comum','url':'https://example.com'}]
    assert find_existing_browsers([url])==[]


def test_remote_address_rejected_without_request(monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('Não deveria consultar rede externa.')
    monkeypatch.setattr('ceab.discovery._local_json',forbidden)
    assert inspect_browser('http://example.com:9222') is None


def process_fixture(root,pid,name,args):
    process=root/str(pid)
    process.mkdir()
    (process/'comm').write_text(name)
    (process/'cmdline').write_bytes('\0'.join(args).encode())


def test_linux_detects_brave_declared_port_and_dynamic_port_only(tmp_path):
    proc=tmp_path/'proc'
    proc.mkdir()
    profile=tmp_path/'profile'
    profile.mkdir()
    (profile/'DevToolsActivePort').write_text('9345\n/session-not-returned\n')
    process_fixture(proc,1,'brave',['brave','--remote-debugging-port=9333'])
    process_fixture(proc,2,'brave',['brave','--remote-debugging-port','0','--user-data-dir',str(profile)])
    process_fixture(proc,3,'brave',['brave','--type=renderer','--remote-debugging-port=9444'])
    process_fixture(proc,4,'other',['other','--remote-debugging-port=9555'])
    process_fixture(proc,5,'brave',['brave'])  # sessão normal não oferece CDP
    assert running_browser_urls(proc)=={'http://127.0.0.1:9333','http://127.0.0.1:9345'}


def test_panel_does_not_start_browser_when_nothing_is_found(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('CEAB_STATE_PATH',str(tmp_path/'state.db'))
    monkeypatch.setattr('ceab.discovery.find_existing_browsers',lambda:[])
    def forbidden(*args,**kwargs): raise AssertionError('Navegador não deve ser iniciado.')
    monkeypatch.setattr('subprocess.Popen',forbidden)
    ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    ui.selectbox(key='connection_mode').set_value('Navegador já aberto (avançado)').run()
    assert not ui.exception
    assert next(b for b in ui.button if b.label=='Iniciar').disabled
    assert any('Nenhuma aba' in i.value for i in ui.info)


def test_panel_can_find_existing_tab(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('CEAB_STATE_PATH',str(tmp_path/'state.db'))
    monkeypatch.setattr('ceab.discovery.find_existing_browsers',lambda:[{'endpoint':'http://127.0.0.1:9333','browser':'Brave','tabs':[{'title':'eproc','queue':True}]}])
    ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    ui.selectbox(key='connection_mode').set_value('Navegador já aberto (avançado)').run()
    assert not ui.exception
    assert not next(b for b in ui.button if b.label=='Iniciar').disabled
    assert any('sessão' in i.value for i in ui.success)


def test_diagnostic_distinguishes_closed_port_from_missing_tab(cdp_server,monkeypatch):
    from ceab.discovery import diagnose_browser
    url,targets,_=cdp_server
    targets[:]=[]
    assert diagnose_browser(url)['status']=='SEM_ABA_EPROC'
    def unavailable(*args): raise ConnectionRefusedError()
    monkeypatch.setattr('ceab.discovery._local_json',unavailable)
    assert diagnose_browser(url)['status']=='CDP_INACESSIVEL'
    assert diagnose_browser('http://example.com')['status']=='ENDERECO_INVALIDO'


def test_diagnostic_keeps_valid_tab_when_another_url_is_malformed(cdp_server):
    from ceab.discovery import diagnose_browser
    url,targets,_=cdp_server
    targets.insert(0,{'type':'page','title':'Outra aba','url':'http://[invalido'})
    diagnosis=diagnose_browser(url)
    assert diagnosis['status']=='ABA_ENCONTRADA'
    assert len(diagnosis['match']['tabs'])==1
