from dataclasses import replace
from decimal import Decimal
import pytest
from ceab.state import State
from ceab.models import AutomationError, Benefit
from ceab.rules import decide
from ceab.worker import Worker
from ceab.eproc import Eproc
from conftest import NUMBERS

B = Benefit(NUMBERS[0],'CONCESSAO','Teste','22/04/2025','01/09/2026',amount=Decimal('100'))


def prepared(state,run,b=B):
    state.add_process(run,b.process)
    state.update_process(b.process,'PREENCHIDO',read_data=b.to_dict(),decision=decide(b).to_dict())


def test_approval_is_atomic_and_claim_is_unique(tmp_path):
    state = State(tmp_path/'state.db')
    run = state.create_execution(False)
    prepared(state,run)
    b2 = replace(B,process=NUMBERS[1])
    prepared(state,run,b2)
    invalid = replace(b2,dip='31/02/2026')
    with pytest.raises(AutomationError): state.approve(run,{B.process:B.to_dict(),b2.process:invalid.to_dict()})
    assert state.process(B.process)['status']=='PREENCHIDO'
    state.approve(run,{B.process:B.to_dict()})
    assert state.claim_send(B.process)
    assert not State(state.path).claim_send(B.process)
    with pytest.raises(ValueError): state.approve(run,{B.process:B.to_dict()})
    assert not state.add_process(run,B.process)


def test_single_execution_and_no_non_date_edits(tmp_path):
    state = State(tmp_path/'state.db')
    run = state.create_execution()
    with pytest.raises(ValueError): state.create_execution()
    prepared(state,run)
    with pytest.raises(ValueError): state.approve(run,{B.process:replace(B,nb='7257466042').to_dict()})
    assert state.process(B.process)['status']=='PREENCHIDO'


def test_worker_full_dry_run_isolates_errors(context,site,tmp_path):
    url,counts = site
    context.new_page().goto(url+'/queue')
    state = State(tmp_path/'state.db')
    run = state.create_execution(True)
    adapter = Eproc(context,lambda n,a,v=None,r='OK':state.log(run,n,a,v,r))
    worker = Worker(state,run,adapter)
    for i in range(3):
        assert worker.step()
        assert len(context.pages)==i+2  # fila + somente os processos já preparados
    assert [r['status'] for r in state.processes(run)] == ['PREENCHIDO','PREENCHIDO','ERRO_LEITURA']
    assert state.process(NUMBERS[2])['error_code']=='TIPO_DESCONHECIDO'
    state.command(run,'pausar')
    assert worker.step() and worker.paused
    assert state.execution(run)['status']=='PAUSADO'
    rows = state.processes(run)[:2]
    state.approve(run,{r['number']:r['read_data'] for r in rows})
    assert worker.step()  # continua pausado; aprovar não equivale a retomar
    assert state.process(NUMBERS[0])['status']=='APROVADO'
    state.command(run,'retomar')
    assert worker.step()
    assert worker.step()
    assert counts == {'sent':0,'minute':0}
    assert all(state.process(n)['simulated'] for n in NUMBERS[:2])
    assert all(state.process(n)['status']=='FINALIZADO' for n in NUMBERS[:2])
    state.command(run,'resolver',{'number':NUMBERS[2]})
    assert not worker.step()
    assert len(context.pages) == 4  # simulação preserva abas
    assert b'ENVIO_SIMULADO' in state.export_csv()


def test_cancel_during_send_waits_and_no_duplicate(tmp_path):
    state = State(tmp_path/'state.db')
    run = state.create_execution(False)
    prepared(state,run)
    state.approve(run,{B.process:B.to_dict()})
    class Page:
        closed=False
        def close(self): self.closed=True
    page = Page()
    class Adapter:
        calls=0
        def submit(self,page,benefit,test_mode,before_click):
            assert before_click()
            self.calls+=1
            state.command(run,'cancelar')  # solicitado durante a operação atômica
            assert state.execution(run)['status']!='CANCELADO'
            return True
    adapter=Adapter()
    worker=Worker(state,run,adapter)
    worker.pages[B.process]=page
    worker.queue_loaded=True
    assert worker.step()
    assert state.process(B.process)['status']=='FINALIZADO'
    assert page.closed
    assert not worker.step()
    worker.send(B.process)
    assert adapter.calls == 1
    assert state.process(B.process)['sent_at']


def test_failed_minute_is_already_sent_and_pending(tmp_path):
    state=State(tmp_path/'state.db')
    run=state.create_execution(False)
    b=replace(B,amount=None)
    prepared(state,run,b)
    state.approve(run,{B.process:b.to_dict()})
    class Adapter:
        def submit(self,page,benefit,test_mode,before_click):
            assert before_click()
            return True
        def minute(self,*args): raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Minutar ausente.')
    worker=Worker(state,run,Adapter())
    worker.pages[B.process]=object()
    worker.send(B.process)
    row=state.process(B.process)
    assert row['status']=='MINUTAR_PENDENTE' and row['sent_at']
    assert not state.claim_send(B.process)


def test_simulation_can_be_reread_but_real_attempt_cannot(tmp_path):
    state=State(tmp_path/'state.db')
    first=state.create_execution(True)
    prepared(state,first)
    state.update_process(B.process,'FINALIZADO',simulated=1)
    state.execution_update(first,status='CONCLUIDO')
    second=state.create_execution(False)
    assert state.add_process(second,B.process)
    assert state.process(B.process)['approved_data'] is None
    state.update_process(B.process,'PREENCHIDO',read_data=B.to_dict(),decision=decide(B).to_dict())
    state.approve(second,{B.process:B.to_dict()})
    assert state.claim_send(B.process)
    state.execution_update(second,status='ERRO')
    third=state.create_execution(False)
    assert not state.add_process(third,B.process)
    assert state.process(B.process)['send_started_at']


def test_session_expiry_pauses_and_restores_source_before_reread(tmp_path):
    state=State(tmp_path/'state.db')
    run=state.create_execution()
    state.add_process(run,B.process)
    class Page:
        def goto(self,url,**kwargs): self.restored=url
    page=Page()
    class Adapter:
        calls=0
        def read_document(self,page,number):
            self.calls+=1
            if self.calls==1: raise AutomationError('SESSAO_EXPIRADA','Sessão expirada.')
            return B,'Proposta'
        def fill(self,*args): pass
    adapter=Adapter()
    worker=Worker(state,run,adapter)
    worker.pages[B.process]=page
    worker.source_urls[B.process]='source-page'
    worker.pending.append(B.process)
    worker.queue_loaded=True
    assert worker.step()
    assert worker.paused
    assert state.process(B.process)['error_code']=='SESSAO_EXPIRADA'
    assert worker.step()
    assert state.execution(run)['status']=='PAUSADO'
    state.command(run,'retomar')
    assert worker.step()
    assert page.restored=='source-page'
    assert state.process(B.process)['status']=='PREENCHIDO'


def test_worker_reads_first_before_identifying_next_and_skips_failed_tab(tmp_path):
    state=State(tmp_path/'state.db')
    run=state.create_execution()
    calls=[]
    class Page:
        def __init__(self,label): self.url=label
    pages=[Page(str(i)) for i in range(3)]
    class Adapter:
        def queue_items(self): return pages
        def open_process(self,item): return item
        def identify(self,page):
            calls.append('identify:'+page.url)
            if page is pages[1]: raise AutomationError('PROCESSO_DIVERGENTE','Página lenta.')
            return NUMBERS[int(page.url)]
        def read_document(self,page,number):
            calls.append('read:'+page.url)
            return replace(B,process=number),'Proposta'
        def fill(self,*args): pass
    worker=Worker(state,run,Adapter())
    assert worker.step()
    assert calls==['identify:0','read:0']
    assert state.process(NUMBERS[0])['status']=='PREENCHIDO'
    assert worker.step()
    assert worker.step()
    assert calls==['identify:0','read:0','identify:1','identify:2','read:2']
    assert state.process(NUMBERS[2])['status']=='PREENCHIDO'


@pytest.mark.parametrize('status',['ERRO_LEITURA','ERRO_PREENCHIMENTO','IGNORADO'])
def test_failed_unsent_process_can_be_reread_in_new_execution(tmp_path,status):
    state=State(tmp_path/'state.db')
    first=state.create_execution()
    prepared(state,first)
    state.update_process(B.process,status,error_code='DOCUMENTO_ILEGIVEL',message='Tabela ausente')
    state.execution_update(first,status='CANCELADO')
    second=state.create_execution()
    assert state.add_process(second,B.process)
    row=state.process(B.process)
    assert row['status']=='NA_FILA' and row['execution_id']==second
    assert row['approved_data'] is None and row['read_data'] is None
    assert b'REINICIAR_SEM_ENVIO' in state.export_csv()
    assert b'Tabela ausente' in state.export_csv()


def test_error_with_reserved_send_never_becomes_retryable(tmp_path):
    state=State(tmp_path/'state.db')
    first=state.create_execution(False)
    prepared(state,first)
    state.approve(first,{B.process:B.to_dict()})
    assert state.claim_send(B.process)
    state.update_process(B.process,'ERRO_LEITURA',error_code='DOCUMENTO_ILEGIVEL')
    state.execution_update(first,status='ERRO')
    second=state.create_execution()
    assert not state.add_process(second,B.process)
    assert 'tentativa de envio' in state.notices(second)[0]['result']
