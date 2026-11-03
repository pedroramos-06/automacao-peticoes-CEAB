"""Painel local; nunca importa ou executa Playwright."""
import re
from urllib.parse import urlparse
import streamlit as st
from ceab.launcher import launch, probe_cdp, recover_dead_worker
from ceab.discovery import find_existing_browsers, diagnose_browser
from ceab.models import AutomationError, Benefit
from ceab.rules import decide, normalize_date
from ceab.state import ACTIVE, State

st.set_page_config(page_title='CEAB/DJ · Conferência',page_icon='⚖️',layout='wide')
state = State()
recover_dead_worker(state)
st.title('Requisições CEAB/DJ')
st.caption('eproc TRF6 · preenchimento assistido com conferência humana · dados mantidos localmente')
@st.fragment(run_every=2)
def browser_controls():
    latest = state.execution()
    active = bool(latest and latest['status'] in ACTIVE)
    browser_open = bool(latest and latest['browser_open'])
    mode = st.selectbox('Conexão do navegador',('Chrome do sistema (login salvo)','Navegador já aberto (avançado)'),
                        index=1 if latest and (active or browser_open) and latest['browser_mode']=='cdp' else 0,
                        disabled=active or browser_open,key='connection_mode')
    managed = mode=='Chrome do sistema (login salvo)'
    test_mode = st.toggle('Modo teste (não intima)',value=True,disabled=active or browser_open,help='Intimar e Apenas salvar nunca são clicados neste modo.')
    if active or browser_open:
        st.caption('Modo desta execução: '+('TESTE' if latest['test_mode'] else 'ENVIO REAL'))
    real_ack = False
    if not test_mode:
        st.warning('Envio real: processos conferidos serão intimados no eproc.')
        real_ack = st.checkbox('Quero permitir envio real após a conferência de cada processo.')
    permitted = test_mode or real_ack
    if managed:
        st.header('Chrome com login salvo')
        st.caption('O sistema abre uma janela própria e reutiliza seu perfil nas próximas sessões. Não é necessário ativar depuração ou informar uma porta.')
        st.caption('Na primeira abertura, faça login no eproc. Se a sessão expirar, entre novamente no mesmo perfil.')
        if st.button('Abrir Chrome',disabled=active or browser_open or not permitted,width='stretch'):
            try:
                launch(state,test_mode)
                st.rerun()
            except (ValueError,OSError) as exc: st.error(str(exc))
        if browser_open:
            st.success('Navegador aberto com o perfil salvo.')
        if st.button('Iniciar processamento',type='primary',disabled=not active or not browser_open or bool(latest and latest['queue_started']),width='stretch'):
            try:
                state.command(latest['id'],'iniciar')
                st.rerun()
            except ValueError as exc: st.error(str(exc))
        if browser_open and st.button('Encerrar navegador',width='stretch'):
            try:
                state.command(latest['id'],'fechar_navegador')
                st.rerun()
            except ValueError as exc: st.error(str(exc))
    else:
        st.header('Aproveitar sessão aberta')
        if 'browser_choices' not in st.session_state:
            st.session_state.browser_choices = find_existing_browsers()
        if st.button('Procurar aba aberta',disabled=active,width='stretch'):
            st.session_state.browser_choices = find_existing_browsers()
        choices = st.session_state.browser_choices
        selected = None
        if choices:
            labels = [f"{c['browser']} · porta {urlparse(c['endpoint']).port} · {len(c['tabs'])} aba(s) eproc" for c in choices]
            index = st.selectbox('Navegador encontrado',range(len(choices)),format_func=lambda i:labels[i],disabled=active)
            selected = choices[index]
            st.success('Aba existente encontrada. A sessão desse navegador será reutilizada.')
            for tab in selected['tabs']: st.caption(tab['title'])
        with st.expander('Conexão manual / aba não encontrada'):
            manual = st.checkbox('Informar endereço CDP manualmente',disabled=active)
            manual_url = st.text_input('Endereço CDP',value='http://localhost:9222',disabled=active)
            st.caption('Somente este modo avançado exige que o Chrome existente tenha sido iniciado com depuração remota.')
        if manual or not selected:
            diagnosis = diagnose_browser(manual_url)
            selected = diagnosis['match']
            if selected:
                st.success(diagnosis['message'])
                for tab in selected['tabs']: st.caption(tab['title'])
            else:
                st.info('Nenhuma aba do eproc acessível foi encontrada no navegador local.')
                st.warning(diagnosis['message'])
        cdp_url = latest['cdp_url'] if active else selected['endpoint'] if selected else None
        connected = bool(selected) if not active else probe_cdp(cdp_url)[0]
        if st.button('Iniciar',type='primary',disabled=active or not connected or not permitted,width='stretch'):
            try:
                launch(state,test_mode,cdp_url)
                st.rerun()
            except (ValueError,OSError) as exc: st.error(str(exc))
    st.caption('Abra a Lista de Processos por Localizador. Ao iniciar, o sistema define 100 por página e abre um processo por vez. As abas preenchidas ficam disponíveis para conferência.')

with st.sidebar:
    browser_controls()

if 'screen' not in st.session_state: st.session_state.screen = 'Painel'
screen = st.radio('Tela',('Painel','Conferir formulário'),horizontal=True,key='screen')


def command_button(label,kind,run,disabled=False):
    if st.button(label,disabled=disabled,width='stretch'):
        try: state.command(run,kind)
        except ValueError as exc: st.error(str(exc))


@st.fragment(run_every=2)
def panel():
    recover_dead_worker(state)
    execution = state.execution()
    if not execution:
        st.info('Inicie uma execução para ler e preencher os processos da página atual.')
        return
    run = execution['id']
    active = execution['status'] in ACTIVE
    st.subheader(f"Execução {run} · {execution['status']}")
    st.caption(execution['message'])
    for notice in state.notices(run):
        if notice['action']=='ABAS_ABERTAS':
            values=notice['values_json']
            st.warning(f"Abriram {values['actual']} de {values['expected']} abas. Confira os pop-ups e os processos restantes.")
        elif notice['action']=='ABA_NAO_IDENTIFICADA':
            st.warning('Aba sem número identificável: '+notice['result'])
        else:
            st.info(f"{notice['number']}: {notice['result']}")
    a,b,c,d = st.columns(4)
    with a: command_button('Pausar','pausar',run,not active or execution['status']=='PAUSADO')
    with b: command_button('Retomar','retomar',run,execution['status']!='PAUSADO')
    with c: command_button('Cancelar','cancelar',run,not active)
    with d:
        st.download_button('Exportar auditoria CSV',state.export_csv(),file_name='auditoria-ceab.csv',mime='text/csv',width='stretch')
    rows = state.processes(run)
    cols = st.columns(5)
    counts = [len(rows),sum(r['status']=='PREENCHIDO' for r in rows),sum(r['status']=='APROVADO' for r in rows),sum(r['status']=='FINALIZADO' for r in rows),sum(r['status'].startswith('ERRO_') or r['status']=='MINUTAR_PENDENTE' for r in rows)]
    for col,label,value in zip(cols,['Processos','Para conferir','Aprovados','Finalizados','Erros / pendências'],counts): col.metric(label,value)
    st.dataframe([{'Processo':r['number'],'Tipo':(r['read_data'] or {}).get('kind','—'),'Status':r['status'],'Modo':'Simulado' if r['simulated'] else '—','Código':r['error_code'] or '', 'Mensagem':r['message']} for r in rows],hide_index=True,width='stretch')
    for row in rows:
        if row['status'].startswith('ERRO_') or row['status']=='MINUTAR_PENDENTE':
            with st.container(border=True):
                st.error(f"{row['number']} · {row['error_code']} · {row['message']}")
                if row['sent_at']: st.warning('Já intimado. Falta resolver a minuta; não reenviar a requisição.')
                elif row['send_started_at']: st.warning('Tentativa registrada. Confirme o resultado no eproc; o robô não repetirá o envio.')
                x,y = st.columns(2)
                with x:
                    if st.button('Resolvido manualmente',key='resolve'+row['number'],disabled=not active): state.command(run,'resolver',{'number':row['number']})
                with y:
                    if st.button('Ignorar',key='skip'+row['number'],disabled=not active): state.command(run,'ignorar',{'number':row['number']})
    history = [r for r in state.processes() if r['execution_id'] != run]
    if history:
        with st.expander('Histórico preservado (processos não serão reenviados)'):
            st.dataframe([{'Processo':r['number'],'Execução':r['execution_id'],'Status':r['status'],'Mensagem':r['message']} for r in history],hide_index=True)


@st.fragment(run_every=2)
def review():
    execution = state.execution()
    if not execution:
        st.info('Inicie uma execução no painel.')
        return
    rows = [r for r in state.processes(execution['id']) if r['status']=='PREENCHIDO']
    st.subheader('Conferir formulário')
    st.caption('Revise a proposta e a aba do eproc. Datas editadas serão reaplicadas e verificadas antes do envio.')
    if execution['test_mode']: st.info('Modo teste: a aprovação simula o envio e preserva as abas.')
    def go_panel():
        st.session_state.screen = 'Painel'
    if not rows:
        st.info('Nenhum formulário aguardando conferência. Veja o andamento e os erros no painel.')
        if st.button('Voltar ao painel',on_click=go_panel): st.rerun()
        return
    approvals = {}
    for row in rows:
        number = row['number']
        data = dict(row['read_data'])
        with st.container(border=True):
            st.markdown(f'**Processo {number}**')
            st.caption(row['document'])
            st.write(f"{data['kind']} · {data['species']}")
            st.write('NB: '+(data['nb'] or 'ausente')+' · Valor lido: '+('R$ '+data['amount'] if data['amount'] is not None else 'não informado'))
            cols = st.columns(3)
            for col,field in zip(cols,('dib','dip','dcb')):
                with col:
                    data[field] = st.text_input(field.upper()+' (dd/mm/aaaa)',value=data.get(field) or '',key=f'{number}:{field}')
            data['dcb'] = data['dcb'].strip() or None
            try:
                for field in ('dib','dip'):
                    data[field] = normalize_date(data[field])
                if data['dcb']: data['dcb'] = normalize_date(data['dcb'])
                decision = decide(Benefit.from_dict(data))
                st.write('Pedido: '+decision.event_text)
                st.write('Destino: '+decision.destination_text)
                for warning in decision.warnings: st.warning(warning)
                checked = st.checkbox('Conferi os dados e autorizo o envio',key=f'{number}:approve')
                if checked: approvals[number] = data
            except AutomationError as exc:
                st.error(str(exc))
    if st.button('Enviar conferidos',type='primary',disabled=not approvals or execution['status'] not in ACTIVE):
        try:
            state.approve(execution['id'],approvals)
            st.success(f'{len(approvals)} processo(s) autorizado(s). Acompanhe o painel.')
            st.rerun()
        except (ValueError,AutomationError) as exc: st.error(str(exc))
    if st.button('Voltar ao painel',on_click=go_panel):
        st.rerun()

panel() if screen == 'Painel' else review()
