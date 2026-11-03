"""Worker exclusivo do Playwright, controlado por comandos SQLite."""
import signal
import threading
from collections import deque
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from .browser_session import open_persistent
from .eproc import Eproc
from .models import AutomationError, Benefit
from .state import State, now
from .rules import decide


class Worker:
    def __init__(self,state,run,adapter,auto_start=True):
        self.state, self.run, self.adapter = state,run,adapter
        self.pages = {}
        self.source_urls = {}
        self.pending = deque()
        self.pending_pages = deque()
        self.pending_items = deque()
        self.managed = not auto_start
        self.started = auto_start
        self.close_requested = False
        self.paused = False
        self.cancelled = False
        self.queue_loaded = False
        self.session_paused = False
        self.exit_event = threading.Event()
        self.test_mode = bool(state.execution(run)['test_mode'])

    def audit(self,number,action,values=None,result='OK'):
        self.state.log(self.run,number,action,values,result)

    def controls(self):
        for cmd in self.state.commands(self.run):
            kind,payload = cmd['type'],cmd['payload']
            if kind == 'iniciar':
                self.started = True
                self.state.execution_update(self.run,queue_started=1)
            elif kind == 'fechar_navegador':
                self.cancelled = self.close_requested = True
            elif kind == 'cancelar': self.cancelled = True
            elif kind == 'pausar': self.paused = True
            elif kind == 'retomar':
                self.paused = False
                self.session_paused = False
            elif kind in ('ignorar','resolver'):
                number = payload.get('number')
                row = self.state.process(number)
                if row and row['execution_id'] == self.run and (row['status'].startswith('ERRO_') or row['status'] == 'MINUTAR_PENDENTE'):
                    self.state.update_process(number,'IGNORADO' if kind == 'ignorar' else 'FINALIZADO',message='Ignorado pelo usuário.' if kind == 'ignorar' else 'Resolvido manualmente pelo usuário.')
        self.state.execution_update(self.run,heartbeat=now())
        if self.cancelled:
            self.state.execution_update(self.run,status='CANCELANDO',message='Cancelando em limite seguro; abas preservadas.')
        elif self.paused:
            self.state.execution_update(self.run,status='PAUSADO',message='Sessão expirada: faça login e retome.' if self.session_paused else 'Pausado entre processos; nenhum envio interrompido.')

    def error(self,number,phase,error):
        code = error.code if isinstance(error,AutomationError) else 'ELEMENTO_NAO_ENCONTRADO' if phase == 'PREENCHIMENTO' else 'ERRO_ENVIO' if phase == 'ENVIO' else 'DOCUMENTO_ILEGIVEL'
        # Não persistir URLs de sessão ou stack traces recebidos do navegador.
        message = str(error) if isinstance(error,AutomationError) else f'Falha no navegador durante {phase.lower()}. Confira a aba manualmente.'
        row = self.state.process(number)
        status = 'MINUTAR_PENDENTE' if phase == 'MINUTAR' else f'ERRO_{phase}'
        self.state.update_process(number,status,error_code=code,message=message)
        self.audit(number,'ERRO',{'phase':phase,'code':code},message)
        if code == 'SESSAO_EXPIRADA':
            self.paused = self.session_paused = True
            # O processo atual só poderá voltar à fila se nenhum envio foi reservado.
            if not row['send_started_at'] and not row['sent_at']:
                self.pending.appendleft(number)

    def prepare(self,number):
        page = self.pages[number]
        phase = 'LEITURA'
        try:
            self.state.execution_update(self.run,status='EXECUTANDO',message=f'Lendo {number}.')
            row = self.state.process(number)
            if row['error_code'] == 'SESSAO_EXPIRADA' and number in self.source_urls:
                page.goto(self.source_urls[number],wait_until='domcontentloaded')
            benefit,document = self.adapter.read_document(page,number)
            decision = decide(benefit)
            self.state.update_process(number,'LIDO',read_data=benefit.to_dict(),decision=decision.to_dict(),document=document,error_code=None,message='Proposta lida.')
            phase = 'PREENCHIMENTO'
            self.adapter.fill(page,benefit)
            self.state.update_process(number,'PREENCHIDO',message='Aguardando conferência humana.')
        except Exception as exc:
            self.error(number,phase,exc)

    def send(self,number):
        row = self.state.process(number)
        # Aprovação nunca pode ser inferida do preenchimento ou de um comando genérico.
        if row['status'] != 'APROVADO' or not row['approved_data']: return
        if row['sent_at'] or row['send_started_at']:
            self.state.update_process(number,'ERRO_ENVIO',error_code='ERRO_ENVIO',message='Tentativa anterior registrada; confira manualmente. Não será reenviado.')
            return
        if number not in self.pages:
            self.state.update_process(number,'ERRO_ENVIO',error_code='ELEMENTO_NAO_ENCONTRADO',message='Aba do processo não está associada a este worker.')
            return
        benefit = Benefit.from_dict(row['approved_data'])
        page = self.pages[number]
        phase = 'ENVIO'
        try:
            self.state.execution_update(self.run,status='EXECUTANDO',message=f'Conferindo aprovação de {number}.')
            # Se a pessoa removeu uma DCB anteriormente preenchida pelo robô,
            # limpar somente esse campo autorizado, nunca outros campos do eproc.
            if row['read_data'].get('dcb') and not benefit.dcb:
                from . import selectors as S
                from .eproc import locate
                locate(page,S.BENEFIT).locator(S.FIELDS['dcb']).fill('')
            sent = self.adapter.submit(page,benefit,self.test_mode,lambda:self.state.claim_send(number))
            if not sent:
                if row['decision']['minute']:
                    self.adapter.minute(page,number,True,source_url=self.source_urls.get(number))
                self.state.update_process(number,'FINALIZADO',simulated=1,message='Simulado: Intimar e salvar minuta não foram clicados. Aba preservada.')
                return
            self.state.update_process(number,'ENVIADO',sent_at=now(),message='Envio confirmado pelo critério do eproc.')
            if row['decision']['minute']:
                phase = 'MINUTAR'
                self.adapter.minute(page,number,False)
                self.state.update_process(number,'MINUTADO',message='Minuta salva.')
            page.close()
            self.state.update_process(number,'FINALIZADO',message='Intimado e finalizado.')
        except Exception as exc:
            self.error(number,phase,exc)

    def step(self):
        """Um limite seguro por chamada; comandos nunca interrompem submit()."""
        self.controls()
        if self.cancelled: return False
        if self.paused: return True
        if not self.started:
            self.state.execution_update(self.run,status='CONFERENCIA',message='Navegador aberto com perfil salvo. Faça login se necessário, abra a lista e clique em Iniciar processamento.')
            return True
        if not self.queue_loaded:
            try:
                self.state.execution_update(self.run,status='EXECUTANDO',message='Ajustando a lista para 100 processos. Cada processo será aberto, lido e preenchido antes de abrir o próximo.')
                items = self.adapter.queue_items()
            except AutomationError as exc:
                if exc.code != 'SESSAO_EXPIRADA':
                    if self.managed and exc.code=='ELEMENTO_NAO_ENCONTRADO':
                        self.started = False
                        self.state.execution_update(self.run,status='CONFERENCIA',queue_started=0,message=str(exc)+' Abra ou ajuste a lista e clique em Iniciar processamento novamente.')
                        self.audit(None,'FILA_INDISPONIVEL',{'code':exc.code},str(exc))
                        return True
                    raise
                self.paused = self.session_paused = True
                self.state.execution_update(self.run,status='PAUSADO',message=str(exc))
                self.audit(None,'SESSAO_EXPIRADA',{},str(exc))
                return True
            self.pending_items.extend(items)
            self.queue_loaded = True
        if not self.pending and not self.pending_pages and self.pending_items:
            item = self.pending_items.popleft()
            try:
                self.pending_pages.append(self.adapter.open_process(item))
            except Exception as exc:
                code = getattr(exc,'code','ELEMENTO_NAO_ENCONTRADO')
                message = str(exc) if isinstance(exc,AutomationError) else 'Falha na abertura individual; confira a lista.'
                self.audit(None,'ABA_NAO_IDENTIFICADA',{'code':code},message)
                if code == 'SESSAO_EXPIRADA':
                    self.pending_items.appendleft(item)
                    self.paused = self.session_paused = True
                return True
        # Identificar e ler uma aba por vez; não esperar todas antes da primeira.
        if not self.pending and self.pending_pages:
            page = self.pending_pages.popleft()
            try:
                number = self.adapter.identify(page)
                if self.state.add_process(self.run,number):
                    self.pages[number] = page
                    self.source_urls[number] = page.url
                    self.pending.append(number)
            except Exception as exc:
                code = getattr(exc,'code','PROCESSO_DIVERGENTE')
                message = str(exc) if isinstance(exc,AutomationError) else 'Falha ao carregar a aba; confira manualmente.'
                self.audit(None,'ABA_NAO_IDENTIFICADA',{'code':code},message)
                if code == 'SESSAO_EXPIRADA':
                    self.pending_pages.appendleft(page)
                    self.paused = self.session_paused = True
                return True
            if not self.pending: return True
        if self.pending:
            number = self.pending.popleft()
            if self.state.process(number)['status'] != 'IGNORADO': self.prepare(number)
            return True
        approved = [r for r in self.state.processes(self.run) if r['status'] == 'APROVADO']
        if approved:
            self.send(approved[0]['number'])
            return True
        rows = self.state.processes(self.run)
        unresolved = [r for r in rows if r['status'] not in ('FINALIZADO','IGNORADO')]
        if not unresolved: return False
        self.state.execution_update(self.run,status='CONFERENCIA',message='Confira os formulários; resolva ou ignore os erros no painel.')
        return True

    def run_loop(self):
        self.state.execution_update(self.run,status='EXECUTANDO',connected=1)
        while self.step():
            # Polling de comandos; tempos do navegador usam esperas por elementos/eventos.
            if self.managed:
                try: self.adapter.context.wait_for_event('close',timeout=250)
                except PlaywrightTimeout: pass
                except Exception: self.cancelled = True
            else: self.exit_event.wait(.25)
        self.state.execution_update(self.run,status='CANCELADO' if self.cancelled else 'CONCLUIDO',connected=0,message='Execução cancelada; navegador preservado até você encerrá-lo.' if self.cancelled else 'Todos os processos desta execução foram tratados.')


def run_worker(path,run):
    state = State(path)
    config = state.execution(run)
    if not config or config['status'] != 'INICIANDO':
        raise ValueError('Execução indisponível para iniciar.')
    import os
    if not state.claim_execution(run,os.getpid()):
        raise ValueError('Outro worker já assumiu esta execução.')
    managed = config['browser_mode']=='persistent'
    context = None
    worker = None
    closed = threading.Event()
    def report_error(exc):
        message = str(exc) if isinstance(exc,AutomationError) else (
            'Não foi possível abrir/manter o Chrome do sistema. Confira se Chrome/Chromium está instalado, se há uma sessão gráfica e se o perfil da automação já está aberto.' if managed else
            'Não foi possível conectar/manter o navegador pela porta CDP. Confira a conexão local.')
        state.log(run,None,'WORKER_ERRO',{'code':getattr(exc,'code','NAVEGADOR')},message)
        state.execution_update(run,status='ERRO',connected=0,message=message)
    try:
        with sync_playwright() as playwright:
            try:
                if managed:
                    context = open_persistent(playwright,config)
                    state.execution_update(run,browser_open=1,connected=1)
                    context.on('close',lambda:closed.set())
                    adapter = Eproc(context,lambda n,a,v=None,r='OK': state.log(run,n,a,v,r))
                else:
                    browser = playwright.chromium.connect_over_cdp(config['cdp_url'],timeout=15000,no_defaults=True)
                    adapter = None
                    queue_error = None
                    expired_context = None
                    for existing in browser.contexts:
                        candidate = Eproc(existing,lambda n,a,v=None,r='OK': state.log(run,n,a,v,r))
                        try: candidate.queue_page()
                        except AutomationError as exc:
                            queue_error = exc
                            if exc.code == 'SESSAO_EXPIRADA': expired_context = candidate
                            continue
                        adapter = candidate
                        break
                    if adapter is None: adapter = expired_context
                    if adapter is None:
                        raise queue_error or AutomationError('ELEMENTO_NAO_ENCONTRADO','Lista de Processos por Localizador não encontrada nas abas existentes.')
                worker = Worker(state,run,adapter,auto_start=not managed)
                def stop_signal(*_):
                    worker.cancelled = worker.close_requested = True
                signal.signal(signal.SIGTERM,stop_signal)
                signal.signal(signal.SIGINT,stop_signal)
                if managed:
                    context.on('close',lambda:setattr(worker,'cancelled',True))
                worker.run_loop()
            except Exception as exc:
                report_error(exc)
            finally:
                if managed and context:
                    # Conclusão/cancelamento não fecha as abas. A pessoa encerra
                    # no painel ou na janela, e Chrome grava o perfil no disco.
                    try:
                        while not closed.is_set() and not (worker and worker.close_requested):
                            commands = state.commands(run)
                            if any(cmd['type']=='fechar_navegador' for cmd in commands): break
                            state.execution_update(run,heartbeat=now())
                            try: context.wait_for_event('close',timeout=500)
                            except PlaywrightTimeout: continue
                            except Exception: break
                    finally:
                        try: context.close()
                        except Exception: pass
                        state.execution_update(run,browser_open=0,connected=0)
                # CDP: nunca fechar o navegador que pertence ao usuário.
    except Exception as exc:
        report_error(exc)
        if managed: state.execution_update(run,browser_open=0,connected=0)
