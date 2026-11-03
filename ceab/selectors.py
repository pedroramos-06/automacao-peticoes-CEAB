"""Seletores centralizados. Dentro do pacote para não ocultar selectors da stdlib."""
QUEUE_PAGE_SIZE = '#optPaginacao100'
QUEUE_LIMIT = 100
QUEUE_TOGGLE = '#lnkInfraCheck'
QUEUE_OPEN = "a[onclick*='abreProcessosSelecionadosEmAbas']"
QUEUE_ROWS = "input[type='checkbox'][id^='chkInfraItem']"
DOCUMENTS = 'a.infraLinkDocumento'
DOCUMENT_FRAME = 'iframe#conteudoIframe'
REQUISITION = "a.infraButton:has-text('Requisição CEAB/DJ')"
EVENT = '#selEventoJudicial'
CONFIRM_DOCUMENTS = '#chkSelEventosDocTodos'
DEACTIVATE_LOCATORS = 'label#lblLocDesMarcarTodos'
DESTINATION = '#selNovoLocalizador'
BENEFIT = "fieldset[id^='fldDadosBeneficio']"
FIELDS = {
    'nb': "input[id^='txtNumBeneficio']",
    'dib': "input[id^='txtDataInicioBeneficio']",
    'dip': "input[id^='txtDataInicioPagamento']",
    'dcb': "input[id^='txtDataInicioCessacao']",
}
SUBMIT = 'button#btnSalvar'
ELEMENT_TIMEOUT = 15000
NAVIGATION_TIMEOUT = 30000
