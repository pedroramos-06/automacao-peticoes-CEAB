# Validação da implementação

Ambiente testado: Python 3.12, Chromium 151 e dependências de `requirements.lock`.

- `python -m pytest -q`: **87 testes aprovados, nenhum pulado**, com as amostras
  pessoais disponíveis no diretório local ignorado `samples/private/`.
- Novo modo padrão: worker abre navegador gerenciado com perfil persistente,
  espera comando explícito antes de processar e não exige uma porta CDP.
- Cookie de sessão sintético (sem data de expiração), armazenamento local e
  armazenamento da aba (sessionStorage) preservados após fechar e reabrir
  Chromium no mesmo perfil. A aba autenticada é restaurada sem visitar a URL
  inicial de login; a aba vazia adicional do Chrome não é confundida com ela.
- Paginação com recarga para 100 verificada. O worker abre somente o processo
  atual, lê e preenche antes de abrir o seguinte. A quantidade de abas após
  cada etapa corresponde somente aos processos já preparados; os formulários
  ficam disponíveis para conferência e envio posterior.
- Rotina de abertura com confirmações mantém os testes de seleção parcial e
  de links repetidos. Uma linha alterada após preparar a fila não é aberta.
- Regressão reproduzida no código anterior: confirmação de abertura cancelada
  automaticamente impedia todas as abas. Agora a confirmação é aceita apenas
  durante o clique de abertura; confirmações posteriores continuam sem aceite.
- Links de abertura repetidos não causam ambiguidade, e o link pode ficar fora
  do frame das linhas. Alertas imediatos ou atrasados e falhas de JavaScript
  são relatados sem aguardar 60 segundos por abas que não abrirão.
- Aba aberta inicialmente em about:blank aguarda navegação atrasada. Worker
  lê a primeira antes de abrir a próxima, registra uma aba com falha
  e continua para a seguinte.
- Leitura de HTML carregado por JavaScript no frame do documento, títulos
  quebrados em linhas/caracteres invisíveis, tabelas com título diferente mas
  estrutura única Tipo/DIB/DIP, valores em tabela aninhada e cálculos em
  tabela separada. Tabelas compatíveis múltiplas continuam rejeitadas.
- ERRO_LEITURA, ERRO_PREENCHIMENTO e registros ignorados por erro podem voltar
  numa nova execução sem envio reservado. Dados/aprovação anteriores são
  invalidados e o histórico é preservado. Reserva de envio impede nova
  tentativa mesmo se o status posterior tiver sido alterado para erro.
- Execução completa do worker gerenciado, com conferência e envio simulado,
  sem clicar em Intimar ou salvar minuta. Abas permanecem abertas após terminar;
  o comando Encerrar navegador fecha o contexto e mantém o perfil no disco.
- Perfis de demonstração e produção separados por nome do banco.
- Banco antigo migrado sem perder o histórico; proteções contra reenvio mantidas.
- Falha na navegação inicial não destrói a janela antes do login manual.
- Modo avançado CDP continua testado com descoberta read-only de abas e sessão
  existente. O painel padrão não consulta portas nem pede depuração remota.

Também são verificados extração por células e por layout nos dois PDFs reais,
HTML, datas e notas, NB, valores/honorários, regras de destino, preservação dos
campos fora do escopo, máscaras, frames, fila, seleção da proposta mais recente,
aprovação e edição na UI, sessão expirada, pausa/cancelamento, tentativa única
persistente, erros isolados e minuta sem salvar em modo teste.

Os testes de envio efetivo e gravação de minuta atingem somente páginas locais
com dados sintéticos. Nenhuma requisição foi enviada ao eproc real. Certificado,
token, validade da sessão e controles do TRF6 ainda dependem de validação no
computador do usuário, começando em modo teste. Login salvo não impede expiração
ou novas exigências de autenticação pelo eproc.

A abertura de navegador foi exercitada em modo headless neste ambiente de nuvem.
Na operação local, o modo padrão abre uma janela na sessão gráfica do usuário.
A UI foi testada pelo Streamlit AppTest, sem Playwright no processo da UI.

Os PDFs pessoais, bancos, logs, credenciais e perfis de navegador não fazem parte
do pacote de distribuição. Sem os PDFs, seus seis testes serão pulados;
os demais usam amostras sintéticas versionadas.


Revisão contra o PRD e os novos anexos: os bytes dos dois PDFs enviados são
idênticos aos das amostras privadas testadas. Os nomes estão invertidos em
relação à seção 10 do PRD; os resultados seguem o conteúdo de cada arquivo.
Os campos extraídos batem com o conteúdo: restabelecimento com DIB 03/07/2026,
DIP 01/09/2026, DCB 30/09/2028 e valor 2422.26; concessão com DIB 22/04/2025,
DIP 01/09/2026, DCB ausente e valor 26561.97. Ambos têm destino Expedir RPV.

Foi reproduzido o PREENCHIMENTO_DIVERGENTE nos eventos nativos do HTML fornecido.
A validação agora aceita as formas completas conhecidas dos eventos e dos
localizadores repetidos, incluindo a verificação antes do envio. As páginas
sintéticas e o simulador passaram a reproduzir esses textos nativos. Testes
rejeitam localizadores de nome semelhante. Os PDFs reais foram exercitados
pelo fluxo de navegador, iframe, download, extração e preenchimento, seguido
de envio simulado. Nenhum envio foi realizado no eproc real.

Erros de leitura agora identificam documento, evento e sequência; ausência de
tabela tem teste específico. Os tipos JUD relatados permanecem bloqueados com
TIPO_DESCONHECIDO, conforme o PRD. Isso não confirma que as outras propostas
que falharam sejam iguais aos dois exemplos; sua análise exige conferir o
documento escolhido e os campos disponíveis.
