from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from ceab.extractor import extract_pdf, extract_html
from ceab.models import AutomationError, Benefit
from ceab.rules import decide, RPV, CALCULATION, ARCHIVE

BASE = Benefit('00000000000000000001', 'RESTABELECIMENTO', 'Auxílio', '03/07/2026', '01/09/2026')

@pytest.mark.parametrize('filename,expected', [
 ('exemplo1.pdf', Benefit('60266575520264063816','RESTABELECIMENTO','Auxílio por Incapacidade Temporária','03/07/2026','01/09/2026','30/09/2028','7257466042',Decimal('2422.26'))),
 ('exemplo2.pdf', Benefit('60269892220264063816','CONCESSAO','PENSÃO POR MORTE - SEGURADO ESPECIAL','22/04/2025','01/09/2026',amount=Decimal('26561.97'))),
])
def test_private_pdfs(filename,expected):
    path = Path('samples/private') / filename
    if not path.exists(): pytest.skip('PDF pessoal não distribuído; copie a amostra local para executar.')
    actual = extract_pdf(path.read_bytes(), expected.process)
    assert actual == expected
    assert decide(actual).destination == RPV[0]
    assert decide(actual).event == ('40400695' if actual.kind == 'CONCESSAO' else '40400877')

@pytest.mark.parametrize('changes,destination,minute', [
 ({}, CALCULATION[0], True),
 ({'dib':'01/09/2026'}, ARCHIVE[0],False),
 ({'dib':'02/09/2026'}, ARCHIVE[0],False),
 ({'dib':'01/10/2025','dip':'01/02/2026'},CALCULATION[0],True),
 ({'amount':Decimal('1'),'dib':'01/09/2026'},RPV[0],False),
])
def test_destinations(changes,destination,minute):
    decision = decide(replace(BASE,**changes))
    assert decision.destination == destination
    assert decision.minute == minute
    assert bool(decision.warnings) == ('amount' in changes)

@pytest.mark.parametrize('changes,code', [
 ({'kind':'REVISÃO'},'TIPO_DESCONHECIDO'),
 ({'dib':''},'DATA_AUSENTE_OU_INVALIDA'),
 ({'dip':'31/02/2026'},'DATA_AUSENTE_OU_INVALIDA'),
 ({'amount':Decimal('0')},'DOCUMENTO_ILEGIVEL'),
 ({'amount':Decimal('-1')},'DOCUMENTO_ILEGIVEL'),
])
def test_errors(changes,code):
    with pytest.raises(AutomationError) as exc: decide(replace(BASE,**changes))
    assert exc.value.code == code

HTML = '''<p>NÚMERO: 0000000-00.2026.4.06.0001</p>
<table><tr><th colspan="3">TABELA COM DADOS PARA CUMPRIMENTO</th></tr>
<tr><td>Tipo</td><td>Restabelecimento</td><td></td></tr>
<tr><td>NB</td><td>31/7257466042</td><td></td></tr>
<tr><td>Espécie</td><td>Auxílio</td><td>Previdenciário</td></tr>
<tr><td>Restabelecimento a partir de</td><td>03/07/2026</td><td>DIB originária em 26/09/2024</td></tr>
<tr><td>DIP</td><td>01.09.2026</td><td></td></tr>
<tr><td>DCB</td><td>Regra com 17/06/2015</td><td>30/09/2028</td></tr>
<tr><td>Valor dos atrasados</td><td>VALOR DEVIDO: R$ 2.422,26 COMPOSIÇÃO R$ 9,00</td></tr>
<tr><td>Honorários</td><td>R$ 0,00</td></tr></table>'''

def test_html_cells_and_traps():
    b = extract_html(HTML)
    assert b.dib == '03/07/2026' and b.dcb is None
    assert b.amount == Decimal('2422.26')
    assert b.nb == '7257466042'


def test_divergent_process():
    with pytest.raises(AutomationError, match='Número do documento'):
        extract_html(HTML, '99999999999999999999')


def test_empty_value_does_not_use_note():
    with pytest.raises(AutomationError) as exc:
        extract_html(HTML.replace('<td>03/07/2026</td>', '<td></td>'))
    assert exc.value.code == 'DATA_AUSENTE_OU_INVALIDA'


def test_no_money_does_not_pick_honorarium():
    b = extract_html(HTML.replace('VALOR DEVIDO: R$ 2.422,26 COMPOSIÇÃO R$ 9,00', 'A apurar'))
    assert b.amount is None

@pytest.mark.parametrize('filename,process,dib,dcb,amount',[
 ('exemplo1.pdf','60266575520264063816','03/07/2026','30/09/2028','2422.26'),
 ('exemplo2.pdf','60269892220264063816','22/04/2025',None,'26561.97'),
])
def test_pdf_layout_fallback(filename,process,dib,dcb,amount,monkeypatch):
    import pdfplumber
    path=Path('samples/private')/filename
    if not path.exists(): pytest.skip('PDF pessoal não distribuído.')
    monkeypatch.setattr(pdfplumber.page.Page,'extract_tables',lambda self:[])
    b=extract_pdf(path.read_bytes(),process)
    assert b.dib==dib and b.dcb==dcb and b.amount==Decimal(amount)


@pytest.mark.parametrize('header',[
 'TABELA COM DADOS<br>PARA CUMPRIMENTO',
 'TABELA COM DADOS PARA CUMPRI\u00adMENTO',
 'DADOS PARA IMPLANTAÇÃO DO BENEFÍCIO',
])
def test_structured_table_with_wrapped_or_different_heading(header):
    b=extract_html(HTML.replace('TABELA COM DADOS PARA CUMPRIMENTO',header))
    assert b.dib=='03/07/2026' and b.dip=='01/09/2026'
    assert b.amount==Decimal('2422.26')


def test_two_structured_tables_without_heading_are_ambiguous():
    content=HTML.replace('TABELA COM DADOS PARA CUMPRIMENTO','Dados do benefício')
    with pytest.raises(AutomationError,match='Mais de uma tabela'):
        extract_html(content+content)


def test_nested_value_table_and_separate_calculation_preserve_amount():
    content=HTML.replace('<td>Restabelecimento</td>','<td><table><tr><td>Restabelecimento</td></tr></table></td>')
    content=content.replace('<tr><td>Valor dos atrasados','</table><table><tr><td>Valor dos atrasados')
    b=extract_html(content)
    assert b.kind=='RESTABELECIMENTO' and b.amount==Decimal('2422.26')


def test_heading_split_across_pdf_text_lines():
    from ceab.extractor import text_values,build_benefit
    text='''NÚMERO: 00000000020264060001
TABELA COM DADOS
PARA CUMPRIMENTO
Tipo       CONCESSÃO
Espécie    Benefício sintético
DIB        22/04/2025
DIP        01/09/2026
Valor dos atrasados    VALOR DEVIDO: R$ 100,00
Honorários R$ 0,00'''
    values,amount=text_values(text)
    b=build_benefit(text,values,amount)
    assert b.dib=='22/04/2025' and b.amount==Decimal('100')


@pytest.mark.parametrize('kind',[
 'JUD – IMPLANTAR BENEFICIO – AUXILIO-DOENCA',
 'JUD – IMPLANTAR BENEFICIO – APOSENTADORIA POR INVALIDEZ',
])
def test_jud_types_reported_by_user_remain_outside_prd(kind):
    with pytest.raises(AutomationError) as exc:
        extract_html(HTML.replace('Restabelecimento</td>',kind+'</td>'))
    assert exc.value.code=='TIPO_DESCONHECIDO'
