from dataclasses import replace
from decimal import Decimal
import pytest
from ceab.eproc import Eproc
from ceab.models import AutomationError, Benefit
from ceab.rules import EVENTS
from ceab import selectors as S
from conftest import NUMBERS

B = Benefit(NUMBERS[0],'CONCESSAO','Teste','22/04/2025','01/09/2026',amount=Decimal('100'))


def untouched(page):
    return page.locator('#txtRMI123456789,#txtDataInicioIncapacidade123456789,#txtCID123456789,#chkDippd123456789,input[type=radio],#parte,#prazo').evaluate_all('(els)=>els.map(x=>({value:x.value,checked:x.checked}))')


def test_fill_preserves_fields_and_test_never_submits(context,site):
    url,counts = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    original = untouched(page)
    adapter = Eproc(context)
    adapter.fill(page,B,False)
    assert page.locator(S.FIELDS['dib']).input_value() == B.dib
    assert page.locator('#selLocalizadorDesativar option:checked').count() == 2
    assert page.evaluate('window.changed') == 1
    assert untouched(page) == original
    edited = replace(B,dib='23/04/2025',dcb='30/09/2028')
    assert adapter.submit(page,edited,True) is False
    assert counts['sent'] == 0
    assert page.locator(S.SUBMIT).count() == 1
    assert page.locator(S.FIELDS['dib']).input_value() == edited.dib
    assert page.locator(S.FIELDS['dcb']).input_value() == edited.dcb
    assert untouched(page) == original


def test_submit_confirm_and_minute(context,site):
    url,counts = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    adapter = Eproc(context)
    assert adapter.submit(page,B,False)
    assert counts['sent'] == 1
    adapter.minute(page,B.process,False)
    assert counts['minute'] == 1


def test_validation_alert_is_send_error(context,site):
    url,counts = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    page.evaluate('window.validation=true')
    with pytest.raises(AutomationError, match='Prazo inválido') as exc:
        Eproc(context).submit(page,B,False)
    assert exc.value.code == 'ERRO_ENVIO'
    assert counts['sent'] == 0
    assert page.locator(S.SUBMIT).count() == 1


def test_queue_already_checked_is_not_toggled(context,site):
    url,_ = site
    queue = context.new_page()
    queue.goto(url+'/queue')
    adapter = Eproc(context)
    pages = adapter.open_queue()
    assert len(pages) == 3
    assert not queue.evaluate('window.toggles')
    assert {adapter.identify(p) for p in pages} == set(NUMBERS)
    b,label = adapter.read_document(pages[0],NUMBERS[0])
    assert b.amount == Decimal('100')
    assert 'evento 26' in label
    assert len(context.pages) == 4  # wrapper fechado; fila + 3 processos


def test_partial_queue_becomes_all_checked(context,site):
    url,_ = site
    queue = context.new_page()
    queue.goto(url+'/queue?count=1')
    queue.locator(S.QUEUE_ROWS).uncheck()
    assert len(Eproc(context).open_queue()) == 1
    assert queue.evaluate('window.toggles') == 1
    assert queue.locator(S.QUEUE_ROWS).is_checked()


def test_process_mismatch_blocks_fill(context,site):
    url,_ = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+NUMBERS[1])
    with pytest.raises(AutomationError) as exc: Eproc(context).fill(page,B,False)
    assert exc.value.code == 'PROCESSO_DIVERGENTE'


def test_mask_fallback_and_divergence(context,site):
    url,_ = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    field = page.locator(S.FIELDS['dib'])
    field.evaluate('''el=>{el.addEventListener('blur',()=>{if(!el.dataset.once){el.value='';el.dataset.once='1'}});el.addEventListener('input',()=>{if(el.dataset.once){const n=el.value.replace(/\\D/g,'').slice(0,8);el.value=n.slice(0,2)+(n.length>2?'/'+n.slice(2,4):'')+(n.length>4?'/'+n.slice(4):'')}})}''')
    Eproc(context).fill_verified(field,B.dib,'dib')
    assert field.input_value() == B.dib
    field.evaluate("el=>el.addEventListener('blur',()=>el.value='errado')")
    with pytest.raises(AutomationError) as exc: Eproc(context).fill_verified(field,B.dib,'dib')
    assert exc.value.code == 'PREENCHIMENTO_DIVERGENTE'


def test_text_of_selected_option_must_match(context,site):
    url,_ = site
    page = context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    page.locator(f'option[value="{EVENTS[B.kind][0]}"]').evaluate("el=>el.textContent='Outro evento'")
    with pytest.raises(AutomationError) as exc: Eproc(context).fill(page,B,False)
    assert exc.value.code == 'PREENCHIMENTO_DIVERGENTE'


def test_form_in_iframe(context,site):
    url,counts=site
    page=context.new_page()
    page.goto(url+'/process?num_processo='+B.process)
    page.set_content(f'<iframe src="{url}/form?num_processo={B.process}"></iframe>')
    frame=page.frame_locator('iframe')
    frame.locator(S.EVENT).wait_for()
    Eproc(context).fill(page,B,False)
    assert frame.locator(S.FIELDS['dib']).input_value()==B.dib
    assert not Eproc(context).submit(page,B,True)
    assert counts['sent']==0


def test_newest_first_document_preferred(context,site):
    url,_=site
    page=context.new_page()
    page.goto(url+'/process?num_processo='+B.process)
    page.set_content('''<table>
    <tr><td>Contestação</td><td><a class="infraLinkDocumento" href="/wrapper?SeqDocumento=1&numSeqEvento=40">CONT1</a></td></tr>
    <tr><td><a class="infraLinkDocumento" data-nome="PROACORDO" href="/wrapper?SeqDocumento=1&numSeqEvento=26">PROACORDO1</a></td></tr>
    <tr><td><a class="infraLinkDocumento" data-nome="PROACORDO" href="/wrapper?SeqDocumento=1&numSeqEvento=30">PROACORDO1</a></td></tr>
    <tr><td><a class="infraLinkDocumento" data-nome="PROACORDO" href="/wrapper?SeqDocumento=2&numSeqEvento=50">PROACORDO2</a></td></tr>
    </table>''')
    selected,label=Eproc(context).document_link(page,B.process)
    assert 'numSeqEvento=30' in selected
    assert 'evento 30' in label
    page.locator('[data-nome="PROACORDO"]').evaluate_all('(els)=>els.forEach(el=>el.remove())')
    selected,label=Eproc(context).document_link(page,B.process)
    assert 'numSeqEvento=40' in selected


def test_minute_dry_run_checks_preference_without_saving(context,site):
    url,counts=site
    page=context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    Eproc(context).minute(page,B.process,True,source_url=url+'/process?num_processo='+B.process)
    assert counts['minute']==0
    assert len(context.pages)==1
    assert '/form?' in page.url


def test_stale_login_tab_does_not_hide_authenticated_queue(context,site):
    url,_=site
    context.new_page().goto(url+'/login')
    queue=context.new_page()
    queue.goto(url+'/queue')
    assert Eproc(context).queue_page() is queue


def test_pagination_reload_opens_100_selected_processes(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue?count=1')
    queue.locator(S.QUEUE_PAGE_SIZE).evaluate("""el=>{el.checked=false;el.onclick=()=>location.href='/queue?count=100'}""")
    records=[]
    adapter=Eproc(context,lambda n,a,v=None,r='OK':records.append((a,v)))
    pages=adapter.open_queue()
    assert len(pages)==100
    assert queue.locator(S.QUEUE_ROWS).count()==100
    assert queue.locator(S.QUEUE_PAGE_SIZE).is_checked()
    assert ('ABRIR_PROCESSOS_SELECIONADOS',{'count':100}) in records
    assert len({adapter.identify(p) for p in pages})==100


def test_identify_waits_for_delayed_popup_navigation(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue?count=1')
    queue.evaluate("""url=>{window.abreProcessosSelecionadosEmAbas=()=>{const p=window.open('about:blank');setTimeout(()=>p.location.href=url,300)}}""",url+'/process?num_processo='+B.process)
    adapter=Eproc(context)
    pages=adapter.open_queue()
    assert adapter.identify(pages[0])==B.process
    benefit,_=adapter.read_document(pages[0],B.process)
    assert benefit.process==B.process


def test_partial_selection_opens_all_without_inverting_checked_rows(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue')
    queue.locator(S.QUEUE_ROWS).nth(1).uncheck()
    assert len(Eproc(context).open_queue())==3
    assert all(queue.locator(S.QUEUE_ROWS).nth(i).is_checked() for i in range(3))



def test_queue_accepts_opening_confirmation_and_removes_handler(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue')
    queue.evaluate("""() => {
        const open=window.abreProcessosSelecionadosEmAbas;
        window.abreProcessosSelecionadosEmAbas=()=>{
            if(confirm('Abrir os processos selecionados em abas/janelas?')) open();
        };
    }""")
    pages=Eproc(context).open_queue()
    assert len(pages)==3
    # A permissão para confirmar termina junto com a abertura da fila.
    assert queue.evaluate("confirm('Outra operação?')") is False


@pytest.mark.parametrize('delayed',[False,True])
def test_queue_alert_is_reported_without_waiting_for_nonexistent_popups(context,site,delayed):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue?count=1')
    queue.evaluate("""delayed=>{window.abreProcessosSelecionadosEmAbas=()=>{
        const notify=()=>alert('Selecione processos válidos.');
        if(delayed) setTimeout(notify,50); else notify();
    }}""",delayed)
    with pytest.raises(AutomationError,match='Selecione processos válidos'):
        Eproc(context).open_queue()
    assert len(context.pages)==1
    assert queue.evaluate("confirm('Outra operação?')") is False


def test_duplicate_open_links_do_not_stop_after_selecting(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue')
    queue.evaluate("""()=>{
        const link=document.querySelector('a[onclick*=abreProcessosSelecionadosEmAbas]');
        document.body.appendChild(link.cloneNode(true));
    }""")
    assert len(Eproc(context).open_queue())==3



def test_queue_javascript_error_reports_opening_failure(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue?count=1')
    queue.evaluate("()=>{window.abreProcessosSelecionadosEmAbas=()=>{throw new Error('Falha sintética')}}")
    with pytest.raises(AutomationError,match='erro de JavaScript'):
        Eproc(context).open_queue()
    assert len(context.pages)==1



def test_open_link_outside_frame_of_queue_rows(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue')
    queue.evaluate("""()=>{
        const controls=[...document.querySelectorAll('#optPaginacao100,#lnkInfraCheck,input[type=checkbox]')];
        const iframe=document.createElement('iframe');
        iframe.srcdoc=controls.map(el=>el.outerHTML).join('');
        controls.forEach(el=>el.remove());
        document.body.appendChild(iframe);
    }""")
    queue.frame_locator('iframe').locator(S.QUEUE_ROWS).first.wait_for()
    assert len(Eproc(context).open_queue())==3


def test_sequential_queue_opens_only_one_selected_process_at_a_time(context,site):
    url,_=site
    queue=context.new_page()
    queue.goto(url+'/queue')
    adapter=Eproc(context)
    items=adapter.queue_items()
    assert len(context.pages)==1
    first=adapter.open_process(items[0])
    assert len(context.pages)==2
    assert adapter.identify(first)==NUMBERS[0]
    assert sum(x.is_checked() for x in queue.locator(S.QUEUE_ROWS).all())==1
    second=adapter.open_process(items[1])
    assert len(context.pages)==3
    assert adapter.identify(second)==NUMBERS[1]
    assert not first.is_closed()


def test_reads_html_loaded_by_javascript_in_document_frame(context,site):
    url,counts=site
    counts['dynamic']=True
    page=context.new_page()
    page.goto(url+'/process?num_processo='+B.process)
    b,label=Eproc(context).read_document(page,B.process)
    assert b.dib==B.dib and b.dip==B.dip and b.amount==B.amount
    assert 'evento 26' in label
    assert len(context.pages)==1


def test_queue_row_changed_after_snapshot_is_not_opened(context,site):
    url,_=site
    page=context.new_page()
    page.goto(url+'/queue')
    adapter=Eproc(context)
    items=adapter.queue_items()
    page.locator(S.QUEUE_ROWS).first.evaluate("el=>el.value='outro-processo'")
    with pytest.raises(AutomationError,match='listagem mudou'):
        adapter.open_process(items[0])
    assert len(context.pages)==1


@pytest.mark.parametrize('kind',['CONCESSAO','RESTABELECIMENTO'])
def test_native_eproc_option_labels_with_prefix_and_repeated_locator(context,site,kind):
    from ceab.rules import RPV
    url,_=site
    page=context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    benefit=replace(B,kind=kind)
    event_text={
        'CONCESSAO':'Expedida/certificada a intimação eletrônica - Requisição - Cumprimento - Implantar Benefício',
        'RESTABELECIMENTO':'Expedida/certificada a intimação eletrônica - Requisição - Cumprimento - Restabelecer Benefício por Incapacidade ou Assistencial',
    }[kind]
    page.locator(f'#selEventoJudicial option[value="{EVENTS[kind][0]}"]').evaluate('(el,text)=>el.textContent=text',event_text)
    page.locator(f'#selNovoLocalizador option[value="{RPV[0]}"]').evaluate("el=>el.textContent='Expedir RPV - Expedir RPV'")
    Eproc(context).fill(page,benefit,False)
    assert page.locator(S.FIELDS['dib']).input_value()==benefit.dib


@pytest.mark.parametrize('filename,expected_kind',[
 ('exemplo1.pdf','RESTABELECIMENTO'),('exemplo2.pdf','CONCESSAO')])
def test_real_private_pdf_through_browser_wrapper_and_native_form(context,site,filename,expected_kind):
    from pathlib import Path
    from ceab.extractor import extract_pdf
    from ceab.rules import decide,RPV
    path=Path('samples/private')/filename
    if not path.exists(): pytest.skip('PDF pessoal não distribuído.')
    expected=extract_pdf(path.read_bytes())
    assert expected.kind==expected_kind
    url,counts=site
    counts['pdf_path']=str(path.resolve())
    process=context.new_page()
    process.goto(url+'/process?num_processo='+expected.process)
    adapter=Eproc(context)
    benefit,label=adapter.read_document(process,expected.process)
    assert benefit==expected
    adapter.fill(process,benefit)
    assert process.locator(S.FIELDS['dib']).input_value()==expected.dib
    assert process.locator(S.FIELDS['dip']).input_value()==expected.dip
    assert process.locator(S.FIELDS['dcb']).input_value()==(expected.dcb or '')
    assert process.locator(S.FIELDS['nb']).input_value()==(expected.nb or '')
    assert process.locator(S.EVENT).input_value()==decide(expected).event
    assert process.locator(S.DESTINATION).input_value()==RPV[0]
    assert adapter.submit(process,benefit,True) is False
    assert counts['sent']==0 and counts['minute']==0
    assert len(context.pages)==1


@pytest.mark.parametrize('wrong_text',[
 'Expedir RPV - Para Liquidar',
 'Expedir RPV - Expedir RPV - Para Remeter Turma',
])
def test_similar_native_locator_names_are_rejected(context,site,wrong_text):
    from ceab.rules import RPV
    url,_=site
    page=context.new_page()
    page.goto(url+'/form?num_processo='+B.process)
    page.locator(f'#selNovoLocalizador option[value="{RPV[0]}"]').evaluate('(el,text)=>el.textContent=text',wrong_text)
    with pytest.raises(AutomationError,match='Opção divergente em #selNovoLocalizador'):
        Eproc(context).fill(page,B,False)


def test_missing_table_error_identifies_selected_document(context,site,monkeypatch):
    url,counts=site
    counts['missing_table']=True
    monkeypatch.setattr(S,'NAVIGATION_TIMEOUT',1000)
    process=context.new_page()
    process.goto(url+'/process?num_processo='+B.process)
    records=[]
    adapter=Eproc(context,lambda n,a,v=None,r='OK':records.append((a,v)))
    with pytest.raises(AutomationError,match='PROACORDO · evento 26 · documento 1:') as exc:
        adapter.read_document(process,B.process)
    assert exc.value.code=='DOCUMENTO_ILEGIVEL'
    assert ('DOCUMENTO_SELECIONADO',{'name':'PROACORDO','event':26,'doc':1}) in records
    assert len(context.pages)==1 and not process.is_closed()
