import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import pytest
from playwright.sync_api import sync_playwright
from ceab.rules import EVENTS, RPV, CALCULATION, ARCHIVE

NUMBERS = ['00000000120264060001','00000000220264060001','00000000320264060001']


def proposal(number,amount=True,invalid=False):
    rows = [('Tipo','CONCESSÃO' if not invalid else 'REVISÃO',''),('Espécie','Benefício sintético',''),('DIB (Data de Início do Benefício)','22.04.2025','Nota em 26/09/2024'),('DIP','01/09/2026',''),('DCB','Regra cita 17/06/2015','')]
    return '<p>NÚMERO: '+number+'</p><table><tr><th>TABELA COM DADOS PARA CUMPRIMENTO</th></tr>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in row)+'</tr>' for row in rows)+f'<tr><td>Valor dos atrasados</td><td>{"VALOR DEVIDO: R$ 100,00" if amount else "A apurar"}</td></tr><tr><td>Honorários</td><td>R$ 0,00</td></tr></table>'


def form(number):
    event_options = ''.join(f'<option value="{v}">Expedida/certificada a intimação eletrônica - {t}</option>' for v,t in EVENTS.values())
    destination_options = ''.join(f'<option value="{v}">{t} - {t}</option>' for v,t in (RPV,CALCULATION,ARCHIVE))
    return f'''<html><body><h1>Processo {number}</h1>
    <select id="selEventoJudicial"><option value="null"></option>{event_options}</select>
    <input type="checkbox" id="chkSelEventosDocTodos">
    <select id="selLocalizadorDesativar" multiple><option value="a">Antigo</option><option value="b">Outro</option></select>
    <label id="lblLocDesMarcarTodos" onclick="document.querySelectorAll('#selLocalizadorDesativar option').forEach(x=>x.selected=true)">Marcar todos</label>
    <select id="selNovoLocalizador" onchange="window.changed=(window.changed||0)+1"><option value="null"></option>{destination_options}</select>
    <fieldset id="fldDadosBeneficio123456789">
    <input id="txtNumBeneficio123456789" value="">
    <input id="txtDataInicioBeneficio123456789" value="">
    <input id="txtDataInicioPagamento123456789" value="">
    <input id="txtDataInicioCessacao123456789" value="">
    <input id="txtRMI123456789" value="123,00">
    <input id="txtDataInicioIncapacidade123456789" value="20/01/2024">
    <input id="txtCID123456789" value="preservar">
    <input type="checkbox" id="chkDippd123456789">
    <input type="radio" name="auto" value="N" checked>
    <select id="parte"><option selected>Parte sintética</option></select>
    </fieldset><input id="prazo" value="30">
    <button id="btnSalvar" onclick="if(window.validation){{alert('Prazo inválido');return}};if(confirm('Confirmar?')){{fetch('/sent?num_processo={number}').then(()=>location.href='/process?num_processo={number}')}}">Intimar</button>
    </body></html>'''


@pytest.fixture
def site():
    counts = {'sent':0,'minute':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            number = query.get('num_processo',[NUMBERS[0]])[0]
            code,ctype = 200,'text/html; charset=utf-8'
            if parsed.path == '/queue':
                count = int(query.get('count',['3'])[0])
                popup = ';'.join(f"if((document.getElementById('chkInfraItem{i}') || document.querySelector('iframe')?.contentDocument.getElementById('chkInfraItem{i}')).checked) window.open('/process?num_processo={n}','_blank')" for i,n in enumerate( (NUMBERS + [f'{i:09d}20264060001' for i in range(4,count+1)])[:count]))
                content = '<input type="radio" name="paginacao" id="optPaginacao100" value="100" checked><title>eproc · Lista de Processos por Localizador</title><h1>Lista de Processos por Localizador</h1>'+''.join(f'<input type="checkbox" id="chkInfraItem{i}" checked>' for i in range(count))+'''<a id="lnkInfraCheck" href="#" onclick="window.toggles=(window.toggles||0)+1;document.querySelectorAll('input[type=checkbox]').forEach(x=>x.checked=!x.checked)">Todos</a>'''+f'<a href="#" onclick="abreProcessosSelecionadosEmAbas();">Abrir os processos selecionados em abas/janelas</a><script>function abreProcessosSelecionadosEmAbas(){{{popup}}}</script>'
            elif parsed.path == '/process':
                content = f'<h1>{number}</h1><table><tr><td>Proposta de conciliação</td><td><a class="infraLinkDocumento" data-nome="PROACORDO" href="/wrapper?num_processo={number}&SeqDocumento=1&numSeqEvento=26" target="_blank">PROACORDO1</a></td></tr></table><a class="infraButton" href="/form?num_processo={number}">Requisição CEAB/DJ</a><a href="/minute?num_processo={number}">Minutar</a>'
            elif parsed.path == '/wrapper':
                content = f'<iframe id="conteudoIframe" name="superior" src="/proposal?num_processo={number}"></iframe>'
            elif parsed.path == '/proposal' and counts.get('pdf_path'):
                from pathlib import Path
                self.send_response(200)
                body=Path(counts['pdf_path']).read_bytes()
                self.send_header('Content-Type','application/pdf')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            elif parsed.path == '/proposal':
                content = proposal(number,amount=number != NUMBERS[1],invalid=number==NUMBERS[2])
                if counts.get('missing_table'): content='<html><body>Documento sintético sem tabela de cumprimento.</body></html>'
                if counts.get('dynamic'):
                    content = '<html><body>Carregando documento...<script>setTimeout(()=>document.body.innerHTML='+json.dumps(content)+',150)</script></body></html>'
            elif parsed.path == '/form': content = form(number)
            elif parsed.path == '/sent':
                counts['sent'] += 1
                content = 'OK'
            elif parsed.path == '/minute':
                content = f'<label for="preference">Preferência</label><select id="preference"><option value="p">jef-ato planilha de calculos</option></select><button onclick="fetch(\'/minute-saved\').then(()=>location.href=\'/process?num_processo={number}\')">Apenas salvar</button>'
            elif parsed.path == '/minute-saved':
                counts['minute'] += 1
                content = 'OK'
            elif parsed.path == '/login': content = '<input type="password">'
            else: code,content = 404,'Não encontrado'
            body = content.encode()
            self.send_response(code)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}',counts
    server.shutdown()
    server.server_close()


@pytest.fixture
def context():
    executable = os.environ.get('CEAB_TEST_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
    with sync_playwright() as pw:
        kwargs = {'headless':True,'args':['--disable-popup-blocking']}
        if executable: kwargs['executable_path'] = executable
        browser = pw.chromium.launch(**kwargs)
        context = browser.new_context()
        yield context
        browser.close()
