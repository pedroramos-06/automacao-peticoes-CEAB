"""Controle do subprocesso e diagnóstico CDP, sem importar Playwright."""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from .state import ACTIVE
from .browser_session import find_browser

ROOT = Path(__file__).resolve().parents[1]
_CHILDREN = {}


def validate_cdp(url):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('CDP deve ser HTTP no navegador local, por exemplo http://localhost:9222.')
    return url.rstrip('/')


def probe_cdp(url):
    try:
        url = validate_cdp(url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url+'/json/version',timeout=1) as response:
            info = json.load(response)
        return bool(info.get('webSocketDebuggerUrl')),info.get('Browser','Chromium')
    except Exception:
        return False,'Chromium não conectado. Inicie o navegador com depuração remota.'


def launch(state,test_mode,cdp_url=None, *, executable_path=None, start_url=None, headless=False):
    mode = 'cdp' if cdp_url else 'persistent'
    previous = state.execution()
    if previous and previous['browser_open'] and previous['pid'] and worker_alive(previous['pid']):
        raise ValueError('Encerre o navegador da execução anterior antes de abrir novamente. O perfil e o login serão preservados.')
    url = validate_cdp(cdp_url) if cdp_url else ''
    executable = executable_path or find_browser() if mode=='persistent' else None
    if executable and not Path(executable).is_file(): raise ValueError('Executável do Chrome não encontrado.')
    run = state.create_execution(test_mode,url,browser_mode=mode,executable_path=executable,start_url=start_url,headless=headless)
    try:
        log_path = state.path.parent / f'worker-{run}.log'
        with log_path.open('a') as log:
            child = subprocess.Popen([sys.executable,str(ROOT/'worker.py'),'--db',str(state.path),'--run',str(run)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        log_path.chmod(0o600)
        _CHILDREN[child.pid] = child
        state.execution_update(run,pid=child.pid)
    except Exception:
        state.execution_update(run,status='ERRO',message='Falha ao iniciar subprocesso do worker.')
        raise
    return run


def worker_alive(pid):
    child = _CHILDREN.get(pid)
    if child is not None:
        return child.poll() is None
    if sys.platform == 'win32':
        # os.kill(pid, 0) pode encerrar processos no Windows; usar consulta read-only.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000,False,pid)
        if not handle: return ctypes.get_last_error() == 5  # acesso negado: não presumir morte
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value == 259
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    except PermissionError: return True
    if sys.platform.startswith('linux'):
        try:
            info = Path(f'/proc/{pid}/stat').read_text()
            if info.rsplit(')',1)[1].strip().split()[0] == 'Z': return False
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
            if str(ROOT/'worker.py').encode() not in cmdline: return False
        except FileNotFoundError: return False
        except PermissionError: return True
    return True


def recover_dead_worker(state):
    run = state.execution()
    if not run or (run['status'] not in ACTIVE and not run['browser_open']) or not run['pid']: return
    if not worker_alive(run['pid']):
        state.execution_update(run['id'],status='ERRO',connected=0,browser_open=0,message='Worker interrompido. Abas e registros preservados; tentativas de envio não serão repetidas.')
        state.log(run['id'],None,'WORKER_INTERROMPIDO',{},'Confira os processos manualmente antes de uma nova execução.')
