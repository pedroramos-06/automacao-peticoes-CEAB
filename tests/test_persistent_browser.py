import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from streamlit.testing.v1 import AppTest
from ceab.browser_session import open_persistent
from ceab.launcher import launch
from ceab.state import State,ACTIVE
from test_cdp_integration import eventually


def test_profile_preserves_session_cookie_and_storage_across_reopening(site,tmp_path):
    url,_=site
    config={'profile_path':str(tmp_path/'profile'),'executable_path':shutil.which('chromium'),
            'headless':1,'start_url':url+'/queue'}
    with sync_playwright() as pw:
        first=open_persistent(pw,config)
        first.add_cookies([{'name':'login-sintetico','value':'sessao-existente','url':url}])
        first.pages[0].evaluate("localStorage.setItem('preferencia-sintetica','preservada')")
        first.close()
        second=open_persistent(pw,config)
        try:
            assert any(c['name']=='login-sintetico' and c['value']=='sessao-existente' for c in second.cookies())
            restored=next(p for p in second.pages if p.url==url+'/queue')
            assert restored.evaluate("localStorage.getItem('preferencia-sintetica')")=='preservada'
            assert not (tmp_path/'profile'/'DevToolsActivePort').exists()
        finally: second.close()


def test_managed_worker_waits_for_user_then_processes_without_cdp(site,tmp_path):
    url,counts=site
    state=State(tmp_path/'state.db')
    run=launch(state,True,start_url=url+'/queue',headless=True)
    try:
        eventually(lambda:state.execution(run)['browser_open']==1 and state.execution(run)['status']=='CONFERENCIA')
        assert state.execution(run)['browser_mode']=='persistent'
        assert state.execution(run)['cdp_url']==''
        assert not state.execution(run)['queue_started']
        assert state.processes(run)==[]
        state.command(run,'iniciar')
        eventually(lambda:len(state.processes(run))==3 and all(r['status'] in ('PREENCHIDO','ERRO_LEITURA') for r in state.processes(run)))
        state.approve(run,{r['number']:r['read_data'] for r in state.processes(run) if r['status']=='PREENCHIDO'})
        eventually(lambda:sum(r['status']=='FINALIZADO' for r in state.processes(run))==2)
        error=next(r for r in state.processes(run) if r['status']=='ERRO_LEITURA')
        state.command(run,'ignorar',{'number':error['number']})
        eventually(lambda:state.execution(run)['status']=='CONCLUIDO')
        assert state.execution(run)['browser_open']==1  # abas permanecem abertas
        assert counts=={'sent':0,'minute':0}
    finally:
        if state.execution(run)['browser_open']:
            state.command(run,'fechar_navegador')
            eventually(lambda:not state.execution(run)['browser_open'])
    assert Path(state.execution(run)['profile_path']).is_dir()
    again=launch(state,True,start_url=url+'/queue',headless=True)
    eventually(lambda:state.execution(again)['browser_open']==1)
    assert state.execution(again)['profile_path']==state.execution(run)['profile_path']
    state.command(again,'fechar_navegador')
    eventually(lambda:not state.execution(again)['browser_open'])


def test_ui_defaults_to_managed_browser_without_port_or_discovery(tmp_path,monkeypatch):
    monkeypatch.setenv('CEAB_STATE_PATH',str(tmp_path/'state.db'))
    def forbidden(*args,**kwargs): raise AssertionError('Modo padrão não deve procurar CDP.')
    monkeypatch.setattr('ceab.discovery.find_existing_browsers',forbidden)
    monkeypatch.setattr('ceab.discovery.diagnose_browser',forbidden)
    ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    assert not ui.exception
    assert ui.selectbox(key='connection_mode').value=='Chrome do sistema (login salvo)'
    assert not next(b for b in ui.button if b.label=='Abrir Chrome').disabled
    assert next(b for b in ui.button if b.label=='Iniciar processamento').disabled
    assert not any(t.label=='Endereço CDP' for t in ui.text_input)


def test_profiles_are_separate_for_demo_and_production(tmp_path):
    production=State(tmp_path/'state.db')
    demo=State(tmp_path/'demo.db')
    a=production.create_execution(browser_mode='persistent')
    b=demo.create_execution(browser_mode='persistent')
    assert production.execution(a)['profile_path']!=demo.execution(b)['profile_path']


def test_legacy_database_is_migrated_without_losing_history(tmp_path):
    import sqlite3
    path=tmp_path/'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE execucao(id INTEGER PRIMARY KEY,status TEXT NOT NULL,test_mode INTEGER NOT NULL,cdp_url TEXT NOT NULL,created_at TEXT NOT NULL,heartbeat TEXT,connected INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT "",pid INTEGER)')
        db.execute('INSERT INTO execucao(id,status,test_mode,cdp_url,created_at) VALUES(42,"CONCLUIDO",1,"http://localhost:9222","historico")')
    state=State(path)
    original=state.execution(42)
    assert original['created_at']=='historico' and original['status']=='CONCLUIDO'
    assert original['browser_mode']=='cdp'
    assert State(path).execution(42)==original


def test_failed_initial_navigation_keeps_browser_available_for_manual_login(tmp_path):
    from playwright.sync_api import Error
    class Page:
        url='about:blank'
        def goto(self,*args,**kwargs): raise Error('Falha de rede sintética.')
    class Context:
        pages=[Page()]
    context=Context()
    class Chromium:
        def launch_persistent_context(self,*args,**kwargs): return context
    class Playwright:
        chromium=Chromium()
    assert open_persistent(Playwright(),{'profile_path':str(tmp_path/'profile'),'headless':True,'start_url':'https://example.invalid'}) is context


def test_reopening_restores_authenticated_tab_without_visiting_start_url(site,tmp_path):
    url,_=site
    config={'profile_path':str(tmp_path/'profile'),'executable_path':shutil.which('chromium'),
            'headless':1,'start_url':url+'/queue'}
    with sync_playwright() as pw:
        first=open_persistent(pw,config)
        first.add_cookies([{'name':'login-sintetico','value':'sessao-existente','url':url}])
        target=url+'/process?num_processo=00000000120264060001'
        first.pages[0].goto(target)
        first.pages[0].evaluate("sessionStorage.setItem('autenticacao-sintetica','ativa')")
        first.close()
        # A URL inicial representa um portal que solicitaria login novamente.
        second=open_persistent(pw,{**config,'start_url':url+'/login'})
        try:
            restored=next(p for p in second.pages if p.url==target)
            assert restored.evaluate("sessionStorage.getItem('autenticacao-sintetica')")=='ativa'
            assert any(c['name']=='login-sintetico' for c in second.cookies())
            assert all('/login' not in p.url for p in second.pages)
        finally: second.close()
