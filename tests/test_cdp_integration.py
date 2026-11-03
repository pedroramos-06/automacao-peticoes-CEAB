import shutil
import socket
import threading
import time
import json
import urllib.request
from playwright.sync_api import sync_playwright
from ceab.launcher import launch
from ceab.discovery import find_existing_browsers
from ceab.state import State,ACTIVE


def eventually(predicate,timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate(): return
        threading.Event().wait(.1)
    raise AssertionError('Operação não terminou no tempo esperado.')


def test_separate_worker_over_cdp(site,tmp_path):
    url,counts=site
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    state=State(tmp_path/'state.db')
    run=None
    with sync_playwright() as pw:
        kwargs={'headless':True,'args':[f'--remote-debugging-port={port}','--disable-popup-blocking']}
        if shutil.which('chromium'): kwargs['executable_path']=shutil.which('chromium')
        context=pw.chromium.launch_persistent_context(tmp_path/'profile',**kwargs)
        context.add_cookies([{'name':'sessao-teste','value':'login-existente','url':url}])
        context.pages[0].goto(url+'/queue')
        found=find_existing_browsers([f'http://127.0.0.1:{port}'])
        assert len(found)==1 and found[0]['tabs'][0]['queue']
        assert len(context.pages)==1
        try:
            run=launch(state,True,found[0]['endpoint'])
            eventually(lambda:len(state.processes(run))==3 and all(r['status'] in ('PREENCHIDO','ERRO_LEITURA') for r in state.processes(run)))
            assert state.execution(run)['connected']
            state.command(run,'pausar')
            eventually(lambda:state.execution(run)['status']=='PAUSADO')
            state.approve(run,{r['number']:r['read_data'] for r in state.processes(run) if r['status']=='PREENCHIDO'})
            state.command(run,'retomar')
            eventually(lambda:sum(r['status']=='FINALIZADO' for r in state.processes(run))==2)
            error=next(r for r in state.processes(run) if r['status']=='ERRO_LEITURA')
            state.command(run,'ignorar',{'number':error['number']})
            eventually(lambda:state.execution(run)['status']=='CONCLUIDO')
            assert counts=={'sent':0,'minute':0}
            assert all(r['simulated'] for r in state.processes(run) if r['status']=='FINALIZADO')
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{port}/json/list') as response:
                targets=json.load(response)
            assert sum(t['type']=='page' for t in targets)==4
            assert any(c['name']=='sessao-teste' and c['value']=='login-existente' for c in context.cookies())
        finally:
            if run and state.execution(run)['status'] in ACTIVE:
                state.command(run,'cancelar')
                eventually(lambda:state.execution(run)['status'] not in ACTIVE)
            context.close()
