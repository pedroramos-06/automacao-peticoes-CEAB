"""Etapas sequenciais do eproc. Sem acesso a APIs, login ou campos fora do PRD."""
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import Error as BrowserError, TimeoutError as PlaywrightTimeout
from . import selectors as S
from .extractor import CNJ, digits, extract_html, extract_pdf
from .models import AutomationError
from .rules import decide, normalize


def number_from_url(url):
    query = parse_qs(urlparse(url).query)
    for key in ('num_processo','numProcesso','numProcessoIntimarAps'):
        value = digits(query.get(key,[''])[0])
        if len(value) == 20: return value
    return None


def assert_session(page):
    parsed = urlparse(page.url)
    action = parse_qs(parsed.query).get('acao',[''])[0].lower()
    if re.search(r'(?:^|/)(?:login|logon|autenticacao)(?:[./]|$)', parsed.path, re.I) or action in ('login','autenticar','login_validar'):
        raise AutomationError('SESSAO_EXPIRADA', 'Sessão expirada. Faça login no navegador e retome.')


def locate(page, selector):
    assert_session(page)
    # Escolher um frame que já contém o elemento antes de aguardar sua visibilidade.
    for frame in page.frames:
        locator = frame.locator(selector)
        if locator.count():
            if locator.count() != 1:
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO', f'Seletor ambíguo: {selector}.')
            try:
                locator.wait_for(state='visible', timeout=S.ELEMENT_TIMEOUT)
                return locator
            except PlaywrightTimeout as exc:
                assert_session(page)
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO', f'Elemento não visível: {selector}.') from exc
    try:
        page.locator(selector).wait_for(state='visible',timeout=S.ELEMENT_TIMEOUT)
        return page.locator(selector)
    except PlaywrightTimeout as exc:
        assert_session(page)
        for frame in page.frames[1:]:
            if frame.locator(selector).count(): return locate(page,selector)
        raise AutomationError('ELEMENTO_NAO_ENCONTRADO', f'Elemento ausente: {selector}.') from exc


def role(page, name, roles=('link','button')):
    """Somente papéis/textos exatos para a etapa de minuta ainda não mapeada."""
    assert_session(page)
    for frame in page.frames:
        for kind in roles:
            locator = frame.get_by_role(kind,name=name)
            if locator.count():
                if locator.count() != 1:
                    raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Controle de minuta ambíguo.')
                locator.wait_for(state='visible',timeout=S.ELEMENT_TIMEOUT)
                return locator
    # Espera pelo papel principal, sem inventar controles alternativos.
    locator = page.get_by_role(roles[0],name=name)
    try:
        locator.wait_for(state='visible',timeout=S.ELEMENT_TIMEOUT)
        return locator
    except PlaywrightTimeout as exc:
        assert_session(page)
        for frame in page.frames[1:]:
            for kind in roles:
                if frame.get_by_role(kind,name=name).count(): return role(page,name,roles)
        raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Controle de minuta não encontrado.') from exc


class Eproc:
    def __init__(self, context, audit=lambda *args,**kwargs: None):
        self.context = context
        self.audit = audit
        context.set_default_timeout(S.ELEMENT_TIMEOUT)
        context.set_default_navigation_timeout(S.NAVIGATION_TIMEOUT)

    def queue_page(self):
        expired = None
        for page in self.context.pages:
            if page.is_closed(): continue
            try: assert_session(page)
            except AutomationError as exc:
                expired = exc
                continue
            for frame in page.frames:
                if frame.locator(S.QUEUE_OPEN).count() and 'LISTA DE PROCESSOS POR LOCALIZADOR' in normalize(frame.locator('body').inner_text()):
                    return page
        if expired: raise expired
        raise AutomationError('ELEMENTO_NAO_ENCONTRADO', 'Abra a Lista de Processos por Localizador na sessão já autenticada do navegador.')

    def queue_controls(self):
        page = self.queue_page()
        page.bring_to_front()
        pagination = locate(page,S.QUEUE_PAGE_SIZE)
        pagination.check()
        page.wait_for_load_state('load')
        # Reencontrar controles após a recarga provocada pela paginação.
        locate(page,S.QUEUE_PAGE_SIZE).wait_for(state='visible')
        if not locate(page,S.QUEUE_PAGE_SIZE).is_checked():
            raise AutomationError('PREENCHIMENTO_DIVERGENTE','A listagem não reteve o limite de 100 processos.')
        self.audit(None,'PAGINACAO',{'limit':S.QUEUE_LIMIT},'OK')
        toggle = locate(page,S.QUEUE_TOGGLE)
        frame = toggle.element_handle().owner_frame()
        checks = frame.locator(S.QUEUE_ROWS)
        count = checks.count()
        if not count:
            raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Nenhuma linha de processo encontrada na página atual.')
        if count > S.QUEUE_LIMIT:
            raise AutomationError('ELEMENTO_NAO_ENCONTRADO','A página contém mais de 100 processos. Ajuste a lista para até 100.')
        return page,frame,checks

    def queue_items(self):
        page,frame,checks = self.queue_controls()
        items = [(page,frame,checks.nth(i).get_attribute('id'),checks.nth(i).get_attribute('value'),
                  checks.nth(i).evaluate("el=>el.closest('tr')?.innerText||''"))
                 for i in range(checks.count())]
        self.audit(None,'FILA_IDENTIFICADA',{'count':len(items)},'ABERTURA SEQUENCIAL')
        return items

    def open_process(self,item):
        page,frame,identifier,value,row_text = item
        assert_session(page)
        page.bring_to_front()
        checks = frame.locator(S.QUEUE_ROWS)
        target = checks.and_(frame.locator('[id="'+identifier+'"]'))
        if target.count() != 1 or target.get_attribute('value') != value or target.evaluate("el=>el.closest('tr')?.innerText||''") != row_text:
            raise AutomationError('PROCESSO_DIVERGENTE','A listagem mudou durante a execução. Inicie uma nova fila para conferir as linhas atuais.')
        selected_checks = frame.locator(S.QUEUE_ROWS+':checked')
        for _ in range(selected_checks.count()):
            selected_checks.first.uncheck()
        target.check()
        if frame.locator(S.QUEUE_ROWS+':checked').count() != 1:
            raise AutomationError('PREENCHIMENTO_DIVERGENTE','Não foi possível selecionar somente o processo atual.')
        self.audit(None,'FILA_SELECIONADA',{'count':1},'OK')
        pages = self.open_selected(page,frame,1)
        if len(pages) != 1:
            raise AutomationError('PROCESSO_DIVERGENTE','O eproc abriu mais de uma aba para a seleção individual. Confira a lista.')
        return pages[0]

    def open_queue(self):
        """Abertura em lote mantida para diagnóstico; o worker usa queue_items()."""
        page,frame,checks = self.queue_controls()
        toggle = locate(page,S.QUEUE_TOGGLE)
        count = checks.count()
        if not all(checks.nth(i).is_checked() for i in range(count)):
            # O controle alterna todos: limpar a seleção parcial antes do clique
            # evita desmarcar os processos que já estavam selecionados.
            for i in range(count):
                if checks.nth(i).is_checked(): checks.nth(i).uncheck()
            toggle.click()
        if not all(checks.nth(i).is_checked() for i in range(count)):
            raise AutomationError('PREENCHIMENTO_DIVERGENTE','A seleção não marcou todas as linhas; confira a lista manualmente.')
        self.audit(None,'FILA_SELECIONADA',{'count':count},'OK')
        return self.open_selected(page,frame,count)

    def open_selected(self,page,frame,count):
        # O eproc pode repetir o mesmo link no topo e no rodapé da lista.
        # Preferir o frame da lista, com suporte ao link fora desse frame.
        frames = [frame]+[other for other in page.frames if other != frame]
        candidates = [f.locator(S.QUEUE_OPEN).filter(visible=True) for f in frames]
        opener = next((links.first for links in candidates if links.count()),candidates[0].first)
        try: opener.wait_for(state='visible',timeout=S.ELEMENT_TIMEOUT)
        except PlaywrightTimeout as exc:
            raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Link Abrir os processos selecionados em abas/janelas não está visível na lista.') from exc
        opened = []
        dialogs = []
        script_errors = []
        def capture(new_page): opened.append(new_page)
        def handle_dialog(dialog):
            dialogs.append((dialog.type,dialog.message))
            # Iniciar autoriza abrir abas. Esta permissão não se estende a
            # confirmações de envio ou aos demais controles do eproc.
            if dialog.type == 'confirm': dialog.accept()
            else: dialog.dismiss()
            self.audit(None,'DIALOGO_ABERTURA',{'type':dialog.type},
                       'ACEITO' if dialog.type == 'confirm' else 'DISPENSADO')
        def handle_script_error(error): script_errors.append(True)
        self.context.on('page',capture)
        page.on('dialog',handle_dialog)
        page.on('pageerror',handle_script_error)
        try:
            opener.click()
            self.audit(None,'ABRIR_PROCESSOS_SELECIONADOS',{'count':count},'CLICADO')
            deadline = time.monotonic()+60
            while len(opened) < count:
                if any(kind != 'confirm' for kind,_ in dialogs) or script_errors: break
                remaining = deadline-time.monotonic()
                if remaining <= 0: break
                try: self.context.wait_for_event('page',timeout=min(remaining*1000,500))
                except PlaywrightTimeout: continue
        finally:
            self.context.remove_listener('page',capture)
            page.remove_listener('dialog',handle_dialog)
            page.remove_listener('pageerror',handle_script_error)
        if len(opened) != count:
            self.audit(None,'ABAS_ABERTAS',{'expected':count,'actual':len(opened)},'Quantidade divergente; seguindo com as abertas.')
        if not opened:
            alerts = [message for kind,message in dialogs if kind == 'alert']
            if alerts:
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO','O eproc interrompeu a abertura: '+'; '.join(alerts))
            if script_errors:
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO','A função de abertura do eproc apresentou um erro de JavaScript. Recarregue a lista e tente novamente.')
            if any(kind == 'prompt' for kind,_ in dialogs):
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO','O eproc pediu uma entrada manual durante a abertura. Confira a lista no navegador.')
            raise AutomationError('ELEMENTO_NAO_ENCONTRADO','O link de abertura foi clicado, mas nenhuma aba abriu. Confira os pop-ups e se há processos selecionados na lista do eproc.')
        return opened

    def identify(self,page):
        try:
            # window.open pode emitir uma aba about:blank antes de navegar.
            page.wait_for_url(lambda url: str(url) not in ('about:blank',''),
                              wait_until='domcontentloaded',timeout=S.NAVIGATION_TIMEOUT)
            page.wait_for_load_state('load',timeout=S.NAVIGATION_TIMEOUT)
            page.locator('body').wait_for(state='attached',timeout=S.NAVIGATION_TIMEOUT)
        except BrowserError as exc:
            raise AutomationError('PROCESSO_DIVERGENTE','A aba não terminou de carregar ou foi fechada. Confira-a manualmente.') from exc
        assert_session(page)
        number = number_from_url(page.url)
        if number: return number
        matches = set(digits(m) for m in re.findall(CNJ,page.locator('body').inner_text()))
        if len(matches) != 1:
            raise AutomationError('PROCESSO_DIVERGENTE','Não foi possível identificar inequivocamente o número da aba.')
        return matches.pop()

    def document_link(self,page,number=None):
        assert_session(page)
        if not any(f.locator(S.DOCUMENTS).count() for f in page.frames):
            try: page.locator(S.DOCUMENTS).first.wait_for(state='visible',timeout=S.ELEMENT_TIMEOUT)
            except PlaywrightTimeout: assert_session(page)
        candidates = []
        for frame in page.frames:
            for anchor in frame.locator(S.DOCUMENTS).all():
                href = anchor.get_attribute('href') or ''
                query = parse_qs(urlparse(urljoin(page.url,href)).query)
                try:
                    sequence = int(query.get('numSeqEvento',['0'])[0])
                    document_sequence = int(query.get('SeqDocumento',['0'])[0])
                except ValueError: continue
                event_text = anchor.evaluate("el => (el.closest('tr') || el.parentElement).innerText")
                candidates.append({'href':href,'name':anchor.get_attribute('data-nome') or '', 'event':sequence,'doc':document_sequence,'description':event_text})
        self.audit(number,'DOCUMENTOS_CANDIDATOS',[{k:c[k] for k in ('name','event','doc')} for c in candidates],'LIDOS')
        primary = [c for c in candidates if normalize(c['name']) == 'PROACORDO' and c['doc'] == 1]
        fallback = [c for c in candidates if c['doc'] == 1 and any(x in normalize(c['description']) for x in ('PROPOSTA DE CONCILIACAO','CONTESTACAO'))]
        selected = primary or fallback
        if not selected:
            raise AutomationError('DOCUMENTO_NAO_ENCONTRADO','Proposta de acordo não encontrada entre os documentos do processo.')
        candidate = max(selected,key=lambda c:c['event'])
        label = f"{candidate['name'] or 'Proposta'} · evento {candidate['event']} · documento 1"
        self.audit(number,'DOCUMENTO_SELECIONADO',{'name':candidate['name'],'event':candidate['event'],'doc':1},label)
        return urljoin(page.url,candidate['href']),label

    def read_document(self,page,number):
        assert_session(page)
        url,label = self.document_link(page,number)
        document = self.context.new_page()
        try:
            document.goto(url,wait_until='domcontentloaded')
            assert_session(document)
            iframe = locate(document,S.DOCUMENT_FRAME)
            src = iframe.get_attribute('src')
            if not src:
                raise AutomationError('DOCUMENTO_ILEGIVEL','Iframe do documento não possui endereço.')
            source_url = urljoin(document.url,src)
            if urlparse(source_url).netloc != urlparse(page.url).netloc:
                raise AutomationError('DOCUMENTO_ILEGIVEL','Documento aponta para origem externa; revisão manual necessária.')
            live_frame = iframe.element_handle().content_frame()
            if live_frame:
                try:
                    live_frame.wait_for_load_state('domcontentloaded',timeout=S.NAVIGATION_TIMEOUT)
                    assert_session(live_frame)
                    benefit = extract_html(live_frame.content(),number,rendered_text=live_frame.locator('body').inner_text())
                    self.audit(number,'DOCUMENTO_RENDERIZADO',{},'LIDO NO NAVEGADOR')
                    return benefit,label
                except AutomationError as exc:
                    if exc.code == 'SESSAO_EXPIRADA': raise
                except BrowserError: pass
            for hop in range(3):
                # Não enviar dados/autenticação para referências externas ao eproc.
                if urlparse(source_url).netloc != urlparse(page.url).netloc:
                    raise AutomationError('DOCUMENTO_ILEGIVEL','Documento aponta para origem externa; revisão manual necessária.')
                response = self.context.request.get(source_url,timeout=S.NAVIGATION_TIMEOUT)
                if response.status in (401,403):
                    raise AutomationError('SESSAO_EXPIRADA','Documento inacessível com a sessão atual.')
                if not response.ok:
                    raise AutomationError('DOCUMENTO_ILEGIVEL',f'Falha ao baixar documento (HTTP {response.status}).')
                parsed = urlparse(response.url)
                if 'login' in parsed.path.lower() or parse_qs(parsed.query).get('acao',[''])[0] == 'login':
                    raise AutomationError('SESSAO_EXPIRADA','Sessão expirou ao baixar o documento.')
                content_type = response.headers.get('content-type','').lower()
                if 'application/pdf' in content_type:
                    return extract_pdf(response.body(),number),label
                if 'html' not in content_type:
                    raise AutomationError('DOCUMENTO_ILEGIVEL',f'Tipo de documento não suportado: {content_type}.')
                html = response.text()
                soup = BeautifulSoup(html,'html.parser')
                if soup.select('input[type="password"]'):
                    raise AutomationError('SESSAO_EXPIRADA','Tela de autenticação recebida no lugar do documento.')
                embedded = soup.select_one('embed[src],object[data],iframe[src]')
                if embedded:
                    if hop == 2:
                        raise AutomationError('DOCUMENTO_ILEGIVEL','Limite de dois saltos de incorporação excedido.')
                    source_url = urljoin(response.url,embedded.get('src') or embedded.get('data'))
                    continue
                try: return extract_html(html,number),label
                except AutomationError as exc:
                    if exc.code != 'DOCUMENTO_ILEGIVEL': raise
                    return self.read_rendered_document(document,number,label,exc)
            raise AutomationError('DOCUMENTO_ILEGIVEL','Documento não pôde ser lido.')
        except AutomationError as exc:
            raise AutomationError(exc.code,f'{label}: {exc}') from exc
        finally:
            document.close()
            page.bring_to_front()

    def read_rendered_document(self,document,number,label,original_error):
        # Alguns documentos HTML têm apenas um carregador na resposta HTTP.
        # Esperar o conteúdo real nos frames da janela já autenticada.
        try:
            document.wait_for_function(r"""() => {
                const norm=s=>(s||'').normalize('NFD').replace(/[\u0300-\u036f\u00ad\u200b\ufeff]/g,'').replace(/\s+/g,' ').trim().toUpperCase();
                const ready=w=>{
                    try {
                        const d=w.document;
                        if(d.querySelector('input[type=password]')) return true;
                        const text=norm(d.body?.innerText);
                        if(text.includes('TABELA COM DADOS PARA CUMPRIMENTO') && /\bTIPO\b/.test(text) && /\bDIP\b/.test(text) && /\bDIB\b|RESTABELECIMENTO A PARTIR DE/.test(text)) return true;
                        for(const table of d.querySelectorAll('table')) {
                            const labels=[...table.rows].map(r=>norm(r.cells[0]?.innerText));
                            if(labels.includes('TIPO') && labels.some(s=>s.startsWith('DIB')||s.startsWith('RESTABELECIMENTO A PARTIR DE')) && labels.some(s=>s.startsWith('DIP'))) return true;
                        }
                        for(let i=0;i<w.frames.length;i++) if(ready(w.frames[i])) return true;
                    } catch(e) {}
                    return false;
                };
                return ready(window);
            }""",timeout=S.NAVIGATION_TIMEOUT)
        except PlaywrightTimeout as exc:
            raise AutomationError('DOCUMENTO_ILEGIVEL',str(original_error)+' O conteúdo renderizado também não apresentou uma tabela reconhecível; confira a proposta aberta no navegador.') from exc
        results = []
        errors = []
        for frame in document.frames:
            if urlparse(frame.url).netloc not in ('',urlparse(document.url).netloc): continue
            assert_session(frame)
            if frame.locator('input[type=password]').count():
                raise AutomationError('SESSAO_EXPIRADA','Tela de autenticação recebida no documento renderizado.')
            try:
                benefit = extract_html(frame.content(),number,rendered_text=frame.locator('body').inner_text())
                results.append(benefit)
            except AutomationError as exc: errors.append(exc)
        if len(results) != 1:
            if not results:
                relevant = next((e for e in errors if e.code != 'DOCUMENTO_ILEGIVEL'),None)
                raise relevant or original_error
            raise AutomationError('DOCUMENTO_ILEGIVEL','Mais de um documento de cumprimento nos frames; revisão manual necessária.')
        self.audit(number,'DOCUMENTO_RENDERIZADO',{},'LIDO NO NAVEGADOR')
        return results[0],label

    def select_verified(self,page,selector,value,text):
        locator = locate(page,selector)
        locator.select_option(value=value)
        self.verify_option(locator,selector,value,text)

    def verify_option(self,locator,selector,value,text):
        selected_text = locator.locator('option:checked').inner_text()
        accepted = {normalize(text)}
        if selector == S.EVENT:
            accepted.add(normalize('Expedida/certificada a intimação eletrônica - '+text))
        elif selector == S.DESTINATION:
            accepted.add(normalize(text+' - '+text))
        # Valores e textos completos conhecidos: nomes parecidos não bastam.
        if locator.input_value() != value or normalize(selected_text) not in accepted:
            raise AutomationError('PREENCHIMENTO_DIVERGENTE',f'Opção divergente em {selector}.')

    def fill_verified(self,locator,value,field):
        locator.fill(value)
        locator.blur()
        if locator.input_value() != value:
            locator.fill('')
            locator.press_sequentially(digits(value))
            locator.blur()
        if locator.input_value() != value:
            raise AutomationError('PREENCHIMENTO_DIVERGENTE',f'O campo {field.upper()} não reteve o valor aprovado.')

    def fill(self,page,benefit,open_form=True):
        assert_session(page)
        current = number_from_url(page.url)
        if current and current != benefit.process:
            raise AutomationError('PROCESSO_DIVERGENTE','A aba está em outro processo; preenchimento interrompido.')
        if open_form:
            locate(page,S.REQUISITION).click()
        decision = decide(benefit)
        self.select_verified(page,S.EVENT,decision.event,decision.event_text)
        locate(page,S.CONFIRM_DOCUMENTS).check()
        locate(page,S.DEACTIVATE_LOCATORS).click()
        self.select_verified(page,S.DESTINATION,decision.destination,decision.destination_text)
        block = locate(page,S.BENEFIT)
        for field,selector in S.FIELDS.items():
            value = getattr(benefit,field)
            if value is None: continue
            locator = block.locator(selector)
            if locator.count() != 1:
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO',f'Campo {field.upper()} ausente ou ambíguo.')
            self.fill_verified(locator,value,field)
        self.verify(page,benefit)
        self.audit(benefit.process,'PREENCHER',benefit.to_dict(),'CONFERIDO POR LEITURA')
        return decision

    def verify(self,page,benefit):
        assert_session(page)
        current = number_from_url(page.url)
        if current and current != benefit.process:
            raise AutomationError('PROCESSO_DIVERGENTE','Número do formulário divergiu do processo aprovado.')
        decision = decide(benefit)
        for selector,value,text in [(S.EVENT,decision.event,decision.event_text),(S.DESTINATION,decision.destination,decision.destination_text)]:
            locator = locate(page,selector)
            self.verify_option(locator,selector,value,text)
        if not locate(page,S.CONFIRM_DOCUMENTS).is_checked():
            raise AutomationError('PREENCHIMENTO_DIVERGENTE','Confirmação de documentos sugeridos desmarcada.')
        block = locate(page,S.BENEFIT)
        for field,selector in S.FIELDS.items():
            expected = getattr(benefit,field)
            if expected is not None and block.locator(selector).input_value() != expected:
                raise AutomationError('PREENCHIMENTO_DIVERGENTE',f'{field.upper()} divergiu da aprovação.')

    def submit(self,page,benefit,test_mode=True,before_click=lambda: True):
        page.bring_to_front()
        self.fill(page,benefit,open_form=False)
        if test_mode:
            self.audit(benefit.process,'ENVIO_SIMULADO',benefit.to_dict(),'INTIMAR NÃO CLICADO')
            return False
        button = locate(page,S.SUBMIT)
        if not before_click():
            raise AutomationError('ERRO_ENVIO','Envio já reservado ou concluído; não será repetido.')
        dialogs = []
        def handle(dialog):
            dialogs.append((dialog.type,dialog.message))
            self.audit(benefit.process,'DIALOGO',{'type':dialog.type,'message':dialog.message},'ACEITO')
            dialog.accept()
        page.on('dialog',handle)
        try:
            button.click()
            # Espera de sucesso definida no PRD: botão deixa a página em até 15 s.
            button.wait_for(state='detached',timeout=S.ELEMENT_TIMEOUT)
            assert_session(page)
            alerts = [message for kind,message in dialogs if kind == 'alert']
            if alerts:
                raise AutomationError('ERRO_ENVIO','Alerta de validação: '+'; '.join(alerts))
            self.audit(benefit.process,'INTIMAR',benefit.to_dict(),'BOTÃO DEIXOU A PÁGINA; SEM ALERTAS')
            return True
        except PlaywrightTimeout as exc:
            assert_session(page)
            raise AutomationError('ERRO_ENVIO','; '.join(message for _,message in dialogs) or 'Envio não confirmado em 15 segundos; confira manualmente. Não será repetido.') from exc
        finally:
            page.remove_listener('dialog',handle)

    def minute(self,page,number,test_mode=True,source_url=None):
        preview = None
        try:
            if test_mode:
                if not source_url:
                    raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Endereço original necessário para conferir a minuta em modo teste.')
                # Preservar a aba do formulário; conferir a minuta em aba temporária.
                preview = self.context.new_page()
                preview.goto(source_url,wait_until='domcontentloaded')
                page = preview
            role(page,re.compile(r'^minutar$',re.I)).click()
            preference = role(page,re.compile(r'prefer[eê]ncia',re.I),('combobox',))
            choices = preference.locator('option').all()
            exact = [o for o in choices if normalize(o.inner_text()) == normalize('jef-ato planilha de calculos')]
            if len(exact) != 1:
                raise AutomationError('ELEMENTO_NAO_ENCONTRADO','Preferência jef-ato planilha de calculos ausente ou ambígua.')
            preference.select_option(value=exact[0].get_attribute('value'))
            if normalize(preference.locator('option:checked').inner_text()) != normalize('jef-ato planilha de calculos'):
                raise AutomationError('PREENCHIMENTO_DIVERGENTE','Preferência de minuta divergente.')
            save = role(page,re.compile(r'^apenas salvar$',re.I),('button','link'))
            if test_mode:
                self.audit(number,'MINUTA_SIMULADA',{'preference':'jef-ato planilha de calculos'},'CONFERIDA; APENAS SALVAR NÃO CLICADO')
                return
            save.click()
            save.wait_for(state='detached',timeout=S.ELEMENT_TIMEOUT)
            assert_session(page)
            self.audit(number,'MINUTAR',{},'APENAS SALVAR CONCLUÍDO')
        finally:
            if preview: preview.close()
