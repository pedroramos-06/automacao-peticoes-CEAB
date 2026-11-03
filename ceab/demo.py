"""eproc de demonstração local, exclusivamente com dados sintéticos."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse

SAMPLES=Path(__file__).resolve().parents[1]/'samples'
NUMBERS=['00000000120264060001','00000000220264060001','00000000320264060001']


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed=urlparse(self.path)
        number=parse_qs(parsed.query).get('num_processo',[NUMBERS[0]])[0]
        if number not in NUMBERS:
            self.send_error(400,'Processo sintético inválido')
            return
        if parsed.path in ('/','/queue'):
            popups=';'.join(f"if(document.getElementById('chkInfraItem{i}').checked) window.open('/process?num_processo={n}','_blank')" for i,n in enumerate(NUMBERS))
            body='<input type="radio" name="paginacao" id="optPaginacao100" value="100" checked><title>eproc · Lista de Processos por Localizador</title><h1>Lista de Processos por Localizador</h1><p>DEMONSTRAÇÃO LOCAL · dados sintéticos</p>'+''.join(f'<p><input type="checkbox" id="chkInfraItem{i}">{n}</p>' for i,n in enumerate(NUMBERS))+'''<a id="lnkInfraCheck" href="#" onclick="const xs=[...document.querySelectorAll('input[type=checkbox]')];const all=xs.every(x=>x.checked);xs.forEach(x=>x.checked=!all)">Selecionar todos</a>'''+f'<p><a href="#" onclick="abreProcessosSelecionadosEmAbas()">Abrir os processos selecionados em abas/janelas</a></p><script>function abreProcessosSelecionadosEmAbas(){{{popups}}}</script>'
        elif parsed.path=='/process':
            body=f'<h1>Processo sintético {number}</h1><table><tr><td>Proposta de conciliação</td><td><a class="infraLinkDocumento" data-nome="PROACORDO" href="/wrapper?num_processo={number}&SeqDocumento=1&numSeqEvento=26" target="_blank">PROACORDO1</a></td></tr></table><p><a class="infraButton" href="/form?num_processo={number}">Requisição CEAB/DJ</a></p><a href="/minute?num_processo={number}">Minutar</a>'
        elif parsed.path=='/wrapper': body=f'<iframe id="conteudoIframe" name="superior" src="/proposal?num_processo={number}" style="width:100%;height:90vh"></iframe>'
        elif parsed.path=='/proposal':
            name=['proposta_concessao.html','proposta_calculo.html','proposta_erro.html'][NUMBERS.index(number)]
            body=(SAMPLES/name).read_text()
        elif parsed.path=='/form': body=(SAMPLES/'formulario.html').read_text().replace('__PROCESS_NUMBER__',number)
        elif parsed.path=='/minute': body=f'<label for="pref">Preferência</label><select id="pref"><option value="p">jef-ato planilha de calculos</option></select><button onclick="location.href=\'/process?num_processo={number}\'">Apenas salvar</button>'
        elif parsed.path=='/sent': body='DEMONSTRAÇÃO: envio simulado no servidor local.'
        else:
            self.send_error(404)
            return
        content=body.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    parser=argparse.ArgumentParser(description='Simulador local do eproc com três processos sintéticos.')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Simulador local na porta {args.port}; abra /queue no Chromium dedicado.',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
