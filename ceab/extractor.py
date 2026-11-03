"""Extração local: células de tabelas primeiro; janela de texto como alternativa."""
import io
import re
from decimal import Decimal
import pdfplumber
from bs4 import BeautifulSoup
from .models import AutomationError, Benefit
from .rules import normalize, normalize_date, decide

DATE = r'\d{2}[./]\d{2}[./]\d{4}'
CNJ = r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}'
MONEY = r'R\s*\$\s*([\d.]+,\d{2})'
ANCHOR = 'TABELA COM DADOS PARA CUMPRIMENTO'
ANCHOR_PATTERN = re.compile(r'TABELA\s+COM\s+DADOS\s+PARA\s+CUMPRIMENTO')


def heading_text(text):
    return normalize(re.sub('[\u00ad\u200b\ufeff]','',text or ''))


def is_benefit_table(rows):
    keys = {field_name(row[0]) for row in rows if row}
    return {'kind','dib','dip'} <= keys



def digits(value):
    return re.sub(r'\D', '', value or '')


def process_number(text):
    match = re.search(r'N[ÚU]MERO\s*:?\s*(' + CNJ + r'|\d{20})(?!\d)', text, re.I)
    if not match:
        raise AutomationError('DOCUMENTO_ILEGIVEL', 'Cabeçalho NÚMERO não encontrado no documento.')
    return digits(match[1])


def field_name(label):
    label = normalize(label)
    if label == 'TIPO': return 'kind'
    if label == 'ESPECIE': return 'species'
    if label.startswith('NB'): return 'nb'
    if label.startswith('DIB') or label.startswith('RESTABELECIMENTO A PARTIR DE'): return 'dib'
    if label.startswith('DIP'): return 'dip'
    if label.startswith('DCB'): return 'dcb'
    return None


def table_values(tables):
    # Sem título reconhecível, aceitar somente uma tabela estruturada com
    # Tipo + DIB/Restabelecimento + DIP. Datas avulsas nunca são suficientes.
    if not any(ANCHOR in heading_text(' '.join(c or '' for c in row)) for table in tables for row in table):
        candidates = [table for table in tables if is_benefit_table(table)]
        if len(candidates) > 1:
            raise AutomationError('DOCUMENTO_ILEGIVEL','Mais de uma tabela com Tipo, DIB e DIP; revisão manual necessária.')
        if candidates:
            tables = [[[ANCHOR]]]+tables[tables.index(candidates[0]):]
    values, value_lines = {}, []
    started, collecting_amount, previous = False, False, None
    for table in tables:
        for row in table:
            cells = [re.sub(r'\s+', ' ', c or '').strip() for c in row]
            if ANCHOR in heading_text(' '.join(cells)):
                if started:
                    raise AutomationError('DOCUMENTO_ILEGIVEL', 'Mais de uma tabela de cumprimento; revisão manual necessária.')
                started = True
                continue
            if not started: continue
            label = cells[0] if cells else ''
            if normalize(label).startswith('HONORARIOS'):
                return values, ' '.join(value_lines)
            if normalize(label).startswith('VALOR DOS ATRASADOS'):
                collecting_amount = True
                previous = None
            if collecting_amount:
                # O rótulo pode estar separado de seu valor por várias linhas no PDF.
                value_lines.extend(c for c in cells if c)
                continue
            key = field_name(label)
            if key:
                nonempty = [c for c in cells[1:] if c]
                # Uma coluna de nota nunca substitui a célula vazia de valor em HTML/3 colunas.
                value = (cells[1] if len(cells) in (2, 3) else (nonempty[0] if nonempty else ''))
                if key in values:
                    raise AutomationError('DOCUMENTO_ILEGIVEL', f'Campo repetido: {label}.')
                values[key] = value
                previous = key
                if key == 'kind':
                    for c in nonempty[1:]:
                        if normalize(c).startswith('NB'):
                            values['nb'] = re.sub(r'^NB\s*(?:\(opcional\))?\s*:?\s*', '', c, flags=re.I)
            elif not label and previous and len(cells) > 1:
                nonempty = [c for c in cells[1:] if c]
                # Continuações com nota isolada não fornecem uma data para campos vazios.
                if nonempty and previous in ('species', 'dcb'):
                    values[previous] += ' ' + nonempty[0]
            elif label:
                previous = None
    return values, ' '.join(value_lines)


def text_values(text):
    """Alternativa por colunas de texto/layout, limitada aos dois blocos do PRD."""
    lines = text.splitlines()
    normalized = '\n'.join(heading_text(line) for line in lines)
    matches = list(ANCHOR_PATTERN.finditer(normalized))
    if len(matches) != 1:
        raise AutomationError('DOCUMENTO_ILEGIVEL', 'Tabela de cumprimento ausente ou ambígua.')
    anchor_end = normalized[:matches[0].end()].count('\n')
    block = lines[anchor_end+1:]
    honor = next((i for i,line in enumerate(block) if 'HONORARIOS' in normalize(line)),len(block))
    block = block[:honor]
    label_pattern = re.compile(r'^(Tipo|Espécie(?: de dependente)?|NB(?:\s*\(opcional\))?|DIB(?:\s*\([^)]*\))?|DIP(?:\s*\([^)]*\))?|DCB(?:\s*\([^)]*\))?|Restabelecimento a partir de|Início dos efeitos financeiros|RMI(?:\s*\([^)]*\))?)(?=\s|:|$)', re.I)
    stops = [i for i,line in enumerate(block) if normalize(line).startswith(('RMI','TABELA COM DADOS PARA CALCULO','VALOR DOS ATRASADOS'))]
    benefit_lines = block[:min(stops)] if stops else block
    labels = []
    for i,line in enumerate(benefit_lines):
        match = label_pattern.match(line.strip())
        if match: labels.append((i,match))
    type_row = next(((i,m) for i,m in labels if normalize(m[1]) == 'TIPO'),None)
    if not type_row:
        raise AutomationError('TIPO_DESCONHECIDO','Tipo ausente na tabela de cumprimento.')
    i,match = type_row
    line = benefit_lines[i]
    remainder = line.strip()[match.end():].lstrip(' :')
    first_value = re.split(r'\s{2,}|\s*\|\s*',remainder)[0]
    if not first_value:
        raise AutomationError('DOCUMENTO_ILEGIVEL','Coluna de valores não identificada.')
    value_left = line.find(first_value,line.find(match[1])+len(match[1]))
    # Valores de células centralizadas podem começar antes do valor da linha Tipo.
    for candidate in benefit_lines[i+1:i+12]:
        if candidate.strip() and not label_pattern.match(candidate.strip()):
            indent = len(candidate)-len(candidate.lstrip())
            if indent > len(line)-len(line.lstrip())+15:
                value_left = min(value_left,indent)
    note_left = None
    for candidate in benefit_lines:
        normalized = normalize(candidate)
        if any(x in normalized for x in ('DIB ORIGINARIA','DIA SEGUINTE A DCB','DATA DO OBITO','PREVIDENCIARIO')):
            segments = [(m.start(),m[0]) for m in re.finditer(r'\S(?:.*?\S)?(?=\s{2,}|$)',candidate)]
            if segments:
                position = segments[-1][0]
                if position > value_left+15: note_left = min(note_left or position,position)
    values = {'kind':first_value}
    nb_inline = re.search(r'NB\s*(?:\(opcional\))?\s*:\s*((?:\d{2}/)?\d{10})',remainder,re.I)
    if nb_inline: values['nb']=nb_inline[1]
    for index,(i,match) in enumerate(labels):
        key = field_name(match[1])
        if not key or key == 'kind': continue
        if normalize(match[1]).startswith('ESPECIE DE'): continue
        end = labels[index+1][0] if index+1<len(labels) else len(benefit_lines)
        if key == 'nb': end = i+1
        chunks = []
        if key == 'species' and i>0 and not label_pattern.match(benefit_lines[i-1].strip()):
            preceding = benefit_lines[i-1]
            position = len(preceding)-len(preceding.lstrip())
            if note_left is None or position < note_left:
                chunks.append(re.split(r'\s{2,}',preceding.strip())[0])
        for j in range(i,end):
            row = benefit_lines[j]
            if j == i:
                label_end = row.find(match[1])+len(match[1])
                start = label_end
            else: start = len(row)-len(row.lstrip())
            remainder = row[start:].lstrip(' :')
            position = len(row)-len(remainder)
            if note_left is not None and position >= note_left: continue
            chunk = re.split(r'\s{2,}|\s*\|\s*',remainder)[0]
            if chunk and not re.search(r'Firefox|https?://|\d+ of \d+',chunk): chunks.append(chunk)
        values[key] = ' '.join(chunks)
    normalized_lines = [normalize(line) for line in block]
    money_start = next((i for i,line in enumerate(normalized_lines) if 'VALOR DEVIDO' in line),None)
    if money_start is None:
        money_start = next((i for i,line in enumerate(normalized_lines) if 'TABELA COM DADOS PARA CALCULO' in line),None)
    if money_start is None:
        money_start = next((i for i,line in enumerate(normalized_lines) if 'VALOR DOS ATRASADOS' in line),len(block))
    return values, ' '.join(line.strip() for line in block[money_start:])


def build_benefit(text, values, amount_window, expected_process=None):
    number = process_number(text)
    if expected_process and number != digits(expected_process):
        raise AutomationError('PROCESSO_DIVERGENTE', 'Número do documento diferente da aba do processo.')
    raw_kind = normalize(values.get('kind', ''))
    kind = 'CONCESSAO' if raw_kind.startswith('CONCESS') else 'RESTABELECIMENTO' if raw_kind.startswith('RESTABELEC') else raw_kind
    if kind not in ('CONCESSAO','RESTABELECIMENTO'):
        raise AutomationError('TIPO_DESCONHECIDO', f'Tipo não suportado: {raw_kind or "ausente"}.')
    dates = {}
    for key in ('dib', 'dip', 'dcb'):
        cell = values.get(key, '').strip()
        if key == 'dcb':
            match = re.match(DATE, cell)
            dates[key] = normalize_date(match[0]) if match else None
        else:
            # Remover parênteses da célula; jamais procurar na coluna de nota.
            cell = re.sub(r'\([^)]*\)', '', cell)
            match = re.search(DATE, cell)
            dates[key] = normalize_date(match[0] if match else '')
    nb_raw = values.get('nb', '').strip()
    nb = None
    if nb_raw:
        if not re.fullmatch(r'(?:\d{2}/)?\d{10}', nb_raw):
            raise AutomationError('DOCUMENTO_ILEGIVEL', 'NB inválido na tabela de cumprimento.')
        nb = digits(nb_raw)[-10:]
    # Composição/honorários não fazem parte da janela monetária relevante.
    window = re.split(r'COMPOSI[ÇC][ÃA]O|HONOR[ÁA]RIOS', amount_window, flags=re.I)[0]
    preferred = re.search(r'VALOR\s+DEVIDO\s*:', window, re.I)
    if preferred: window = window[preferred.end():]
    match = re.search(MONEY, window, re.I)
    amount = Decimal(match[1].replace('.', '').replace(',', '.')) if match else None
    benefit = Benefit(number, kind, values.get('species',''), **dates, nb=nb, amount=amount)
    decide(benefit)
    return benefit


def extract_pdf(content: bytes, expected_process=None):
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            tables, texts = [], []
            for page in pdf.pages:
                texts.append(page.extract_text(layout=True) or '')
                tables.extend(page.extract_tables())
            text = '\n'.join(texts)
        values, amount = table_values(tables)
        if not values:
            values, amount = text_values(text)
        return build_benefit(text, values, amount, expected_process)
    except AutomationError:
        raise
    except Exception as exc:
        raise AutomationError('DOCUMENTO_ILEGIVEL', 'PDF não pôde ser lido; confira o documento manualmente.') from exc


def extract_html(content: str, expected_process=None, rendered_text=None):
    soup = BeautifulSoup(content, 'html.parser')
    tables = []
    for table in soup.find_all('table'):
        rows = []
        for row in table.find_all('tr'):
            if row.find_parent('table') is table:
                rows.append([cell.get_text(' ', strip=True) for cell in row.find_all(['td','th'], recursive=False)])
        if not table.find('table') or is_benefit_table(rows) or any(heading_text(' '.join(c or '' for c in row)) == ANCHOR for row in rows):
            tables.append(rows)
    text = rendered_text or soup.get_text('\n', strip=True)
    if len(ANCHOR_PATTERN.findall(heading_text(text))) > 1:
        raise AutomationError('DOCUMENTO_ILEGIVEL','Mais de uma tabela de cumprimento; revisão manual necessária.')
    values, amount = table_values(tables)
    if not values:
        values, amount = text_values(text)
    return build_benefit(text, values, amount, expected_process)
