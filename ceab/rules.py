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


def normalize(text):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFD', text or '') if not unicodedata.combining(c))).strip().upper()


def normalize_date(value):
    value = (value or '').strip().replace('.', '/')
    if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', value):
        raise AutomationError('DATA_AUSENTE_OU_INVALIDA', 'Data obrigatória ausente ou inválida (dd/mm/aaaa).')
    try:
        datetime.strptime(value, '%d/%m/%Y')
    except ValueError as exc:
        raise AutomationError('DATA_AUSENTE_OU_INVALIDA', f'Data inválida: {value}.') from exc
    return value


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
