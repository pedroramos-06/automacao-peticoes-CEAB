import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from .models import AutomationError, Benefit, Decision

EVENTS = {
    'CONCESSAO': ('40400695', 'Requisição - Cumprimento - Implantar Benefício'),
    'RESTABELECIMENTO': ('40400877', 'Requisição - Cumprimento - Restabelecer Benefício por Incapacidade ou Assistencial'),
}
RPV = ('381699451987351783813385015019', 'Expedir RPV')
CALCULATION = ('381705321621977089288983043745', 'Aguarda Prazo Apresentacao de Calculo')
ARCHIVE = ('381708438042580063585092174087', 'Aguarda Implantar Beneficio - Para Arquivar')

# Caracteres invisíveis que a extração de PDF/HTML costuma introduzir nos rótulos e datas.
INVISIBLE_CHARS = str.maketrans('', '', '\u200b\u200c\u200d\u2060\ufeff\u00ad')
# Definição única do formato de data aceito: dia/mês de 1 ou 2 dígitos, separador
# / . ou - (pode misturar) e ano com 4 dígitos ou 2 dígitos (2 dígitos são lidos
# como 20XX). As bordas impedem que a data seja lida dentro de uma sequência
# numérica maior (ex.: número CNJ).
DATE_PATTERN = r'(?<!\d)(?<!\d[./-])(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>\d{4}|\d{2})(?!\d)(?![./-]\d)'


def normalize(text):
    text = (text or '').translate(INVISIBLE_CHARS)
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFD', text) if not unicodedata.combining(c))).strip().upper()


def normalize_date(value):
    text = (value or '').strip()
    match = re.fullmatch(DATE_PATTERN, text)
    if not match:
        raise AutomationError('DATA_AUSENTE_OU_INVALIDA', 'Data obrigatória ausente ou inválida (dd/mm/aaaa).')
    # Ano com 2 dígitos é sempre 20XX; a conversão acontece somente aqui.
    year = match['year']
    year = 2000 + int(year) if len(year) == 2 else int(year)
    try:
        parsed = datetime.strptime(f"{match['day']}/{match['month']}/{year}", '%d/%m/%Y')
    except ValueError as exc:
        raise AutomationError('DATA_AUSENTE_OU_INVALIDA', f'Data inválida: {text}.') from exc
    return parsed.strftime('%d/%m/%Y')


def decide(benefit: Benefit) -> Decision:
    kind = normalize(benefit.kind)
    if kind not in EVENTS:
        raise AutomationError('TIPO_DESCONHECIDO', f'Tipo não suportado: {benefit.kind}.')
    dib = datetime.strptime(normalize_date(benefit.dib), '%d/%m/%Y').date()
    dip = datetime.strptime(normalize_date(benefit.dip), '%d/%m/%Y').date()
    if benefit.dcb:
        normalize_date(benefit.dcb)
    if benefit.nb and not re.fullmatch(r'\d{10}', benefit.nb):
        raise AutomationError('DOCUMENTO_ILEGIVEL', 'NB deve conter exatamente 10 dígitos.')
    warnings = ()
    if benefit.amount is not None:
        if not benefit.amount.is_finite() or benefit.amount <= Decimal('0'):
            raise AutomationError('DOCUMENTO_ILEGIVEL', 'Valor zerado ou inválido: exige revisão manual.')
        destination = RPV
        if dib >= dip:
            warnings = ('Há valor monetário e DIB igual ou posterior à DIP. Confira a proposta.',)
    else:
        destination = CALCULATION if dib < dip else ARCHIVE
    event = EVENTS[kind]
    return Decision(*event, *destination, destination == CALCULATION, warnings)
