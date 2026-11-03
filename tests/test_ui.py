import sys
from pathlib import Path
from decimal import Decimal
from streamlit.testing.v1 import AppTest
from ceab.state import State
from ceab.models import Benefit
from ceab.rules import decide

ROOT=Path(__file__).resolve().parents[1]


def test_panel_without_browser_and_no_playwright_in_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('CEAB_STATE_PATH',str(tmp_path/'state.db'))
    ui=AppTest.from_file(str(ROOT/'app.py')).run()
    assert not ui.exception
    assert ui.toggle[0].value is True
    assert any('Requisições' in t.value for t in ui.title)
    assert not any('playwright' in line for line in (ROOT/'app.py').read_text().splitlines() if line.startswith(('import ','from ')))


def test_review_edits_dates_and_approves(tmp_path,monkeypatch):
    path=tmp_path/'state.db'
    monkeypatch.setenv('CEAB_STATE_PATH',str(path))
    state=State(path)
    run=state.create_execution()
    b=Benefit('00000000120264060001','CONCESSAO','Teste','22/04/2025','01/09/2026',amount=Decimal('100'))
    state.add_process(run,b.process)
    state.update_process(b.process,'PREENCHIDO',read_data=b.to_dict(),decision=decide(b).to_dict(),document='Proposta sintética')
    ui=AppTest.from_file(str(ROOT/'app.py')).run()
    ui.radio[0].set_value('Conferir formulário').run()
    assert not ui.exception
    ui.text_input(key=b.process+':dib').set_value('23/04/2025')
    ui.checkbox(key=b.process+':approve').check().run()
    next(button for button in ui.button if button.label=='Enviar conferidos').click().run()
    assert not ui.exception
    assert state.process(b.process)['approved_data']['dib']=='23/04/2025'
    assert state.process(b.process)['status']=='APROVADO'

    next(button for button in ui.button if button.label=='Voltar ao painel').click().run()
    assert not ui.exception
    assert ui.radio[0].value=='Painel'
