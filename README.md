# Automação da Requisição CEAB/DJ · eproc TRF6

Painel local para ler propostas de acordo, preencher requisições e submetê-las
**somente após conferência humana**. Implementa o PRD v2 de 08/10/2026.
Modo teste vem ligado: não clica em **Intimar** nem em **Apenas salvar**.

## Instalação

Requer Python 3.11 ou superior e Chrome/Chromium. Tudo é executado no computador
que contém a sessão autenticada do eproc. O navegador aberto no seu computador
não é acessível a um worker em uma máquina de nuvem.

Linux/macOS, na raiz do repositório:

```bash
bash scripts/setup.sh
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
```

As dependências diretas estão em `requirements.txt`; `requirements.lock` fixa
as dependências transitivas da instalação testada. Não são necessários tokens
ou APIs do eproc. Não execute o painel em um servidor público.

## Operação padrão: Chrome do sistema com login salvo

Não é necessário iniciar Chrome por comando, ativar depuração remota ou
informar uma porta. O próprio worker abre Chrome/Chromium com um perfil
persistente da aplicação. Playwright usa sua conexão interna de automação;
não expõe uma porta CDP para você configurar.

1. Na raiz do repositório, ative o ambiente e abra o painel:

   ```bash
   streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
   ```

2. Mantenha **Chrome do sistema (login salvo)** selecionado e **Modo teste**
   ligado. Clique em **Abrir Chrome**. Chrome instalado é preferido; se não
   houver, o sistema tenta Chromium instalado ou o Chromium do Playwright.
3. A janela abre o eproc. Na primeira utilização, faça login manualmente
   nessa janela (inclusive certificado/token quando necessário). O painel
   espera; não tenta fazer login nem iniciar a fila automaticamente.
4. Abra **Lista de Processos por Localizador** nessa janela.
   Clique em **Iniciar processamento**. O sistema marca **100** na paginação,
   seleciona **somente um processo** e clica em **Abrir os processos selecionados em abas/janelas**.
   Aguarda essa aba carregar, lê e preenche o formulário, mantém a aba para
   conferência e então abre o próximo processo. As demais linhas ainda não
   abertas permanecem na fila; não há carregamento de todas ao mesmo tempo.
   Confirmações dessa abertura são aceitas automaticamente. Se o eproc
   repetir o link no topo e no rodapé, somente um deles é clicado. Alertas
   ou falhas de JavaScript na abertura aparecem no painel.
   Se uma aba falhar, registra o problema e continua com as demais linhas.
   Não altere a ordenação ou os filtros da lista enquanto a fila estiver rodando.
   Se a lista não estiver disponível,
   o painel explica o problema e permite ajustar a página e iniciar novamente.
5. Em **Conferir formulário**, revise a proposta e as abas, edite DIB/DIP/DCB
   se necessário, marque **Conferi os dados e autorizo o envio** e clique em
   **Enviar conferidos**. As datas aprovadas são reaplicadas e verificadas.
6. Modo teste não clica em Intimar nem salva minuta. Quando há cálculos,
   confere a preferência numa aba temporária sem salvar. Fora do teste,
   somente os processos aprovados são intimados, em sequência.
7. Ao terminar ou cancelar, o navegador continua aberto para conferência.
   Clique em **Encerrar navegador** (ou feche a janela) para encerrar com
   gravação do perfil no disco. Para outra execução, clique em **Abrir Chrome**:
   o mesmo perfil **CEAB/DJ** será usado, sem apagar seu login ou suas preferências.
   As abas do eproc são restauradas sem redirecioná-las à página inicial.

**O login depende da validade da sessão do eproc.** O perfil conserva cookies
(incluindo a restauração de sessão do Chrome), armazenamento local,
armazenamento da aba restaurada e dados do
navegador entre aberturas. Não evita expiração da sessão, exigência de novo
certificado/token, logout ou políticas do servidor. Se o eproc pedir login
novamente, entre na mesma janela e continue.

O perfil da automação é separado do perfil pessoal. Não são copiados cookies
ou credenciais de outras janelas. Para o banco padrão, fica em
`runtime/state-browser-profile/`. Bancos com nomes diferentes têm perfis
próprios (por exemplo, `runtime/demo-browser-profile/` para `runtime/demo.db`).
Não apague esse diretório se quiser manter o login. Não abra simultaneamente
outro Chrome com o mesmo perfil.

**Pausar** termina a operação do processo atual e aguarda entre processos.
**Cancelar** encerra o processamento no próximo limite seguro e mantém o
navegador aberto. **Encerrar navegador** cancela no limite seguro e fecha
somente o Chrome que a aplicação abriu, preservando seu perfil. Não encerre
à força o worker durante um envio. Sessão expirada pausa o processamento:
entre novamente na mesma janela e clique em **Retomar**. Dados relidos exigem
nova conferência.

Um erro de processo não bloqueia os demais. Corrija no eproc e clique em
**Resolvido manualmente**, ou em **Ignorar**. MINUTAR_PENDENTE significa que
já houve intimação e falta somente resolver a minuta; não reenviar.

### Modo avançado: navegador já aberto

A opção **Navegador já aberto (avançado)** mantém compatibilidade com a
integração anterior. Somente essa opção exige CDP em uma sessão já aberta.
Ela procura abas existentes por **Procurar aba aberta**, com porta manual
opcional. Não é necessária para o fluxo padrão acima. Nesse modo, o worker
não fecha o navegador do usuário.

## Estado, privacidade e recuperação

- UI (`app.py`) e worker (`worker.py`) são processos separados. A UI não importa
  Playwright. Os comandos e o estado trafegam por SQLite local em
  `runtime/state.db`, com tabelas `execucao`, `processo`, `comando` e `log`.
- `CEAB_STATE_PATH` permite escolher outro banco local. Use bancos distintos
  para demonstrações sintéticas e operação real. Não apague o banco de uma
  operação real para repetir processos: ele contém as proteções de envio.
- Um registro por número de processo; aprovação e reserva de envio são
  transacionais. Uma tentativa real é persistida **antes** de clicar em
  Intimar. Depois de tentativa, crash ou confirmação ambígua, o robô não tenta
  novamente; a pessoa verifica o resultado no eproc. Registros já intimados
  não voltam para a fila.
- Ao cancelar antes do envio, uma nova execução pode reler os processos
  interrompidos sem reutilizar aprovações. Erros permanecem para resolução
  manual. A auditoria anterior permanece no CSV.
- Dados de processo não são enviados a serviços de IA. O documento é baixado
  com os cookies da sessão apenas da mesma origem do eproc, processado em
  memória e sua aba de leitura é fechada. Referências externas são recusadas.
- Auditoria em UTC exportável em CSV; inclui números e dados de benefício.
  Banco, logs, perfil do navegador, backups e exportações são sensíveis e devem
  ficar no computador autorizado. SQLite não é criptografado.
- O painel indica falhas de abertura do navegador e ausência da lista.
  No modo avançado, informa falhas de CDP; esse acesso fica restrito ao loopback.
- Um worker morto é detectado no painel. Se uma execução não puder ser
  recuperada, seus registros e eventuais reservas de envio são preservados.

## Demonstração sem eproc (modo avançado de teste)

Com o ambiente ativado, execute cada comando em um terminal:

```bash
python -m ceab.demo
python scripts/start_browser.py --url http://127.0.0.1:8765/queue
```

Linux/macOS:

```bash
CEAB_STATE_PATH=runtime/demo.db streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

PowerShell:

```powershell
$env:CEAB_STATE_PATH="runtime/demo.db"
streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

O simulador tem três processos fictícios: um com valor (RPV), um sem valor
(minuta de cálculos) e um tipo desconhecido (erro isolado). O formulário
`samples/formulario.html` reproduz os seletores fornecidos com dados sintéticos
e campos que devem ser preservados. O simulador é servido somente no loopback.

## Testes

```bash
python -m pytest -q
```

Os testes exercitam regras, extração PDF/HTML, máscaras, preservação dos campos
fora de escopo, conferência da UI, fila, envio simulado, confirmação/validação,
minuta, aprovação transacional, pausa/cancelamento, prevenção de reenvio e
worker separado conectado por CDP ao Chromium local e navegador gerenciado
com perfil persistente, fechamento/reabertura e restauração de login sintético. Os cliques de envio real
nos testes atingem exclusivamente o servidor sintético.

Se não houver Chromium do sistema, execute `python -m playwright install
chromium`. `CEAB_TEST_CHROMIUM` pode apontar para outro executável nos testes.

Os PDFs pessoais fornecidos estão em `samples/private/`, ignorados pelo Git.
Em outro checkout, copie-os ali para rodar os testes PDF; sem eles, os testes que dependem deles
são **pulados**, nunca considerados aprovados. Nos anexos recebidos,
`exemplo1.pdf` contém o restabelecimento e `exemplo2.pdf` a concessão, ao contrário
da nomenclatura da tabela do PRD. Os testes respeitam o conteúdo dos anexos.
Há também amostras HTML sintéticas versionadas, sem dados pessoais.

## Limites de validação

O fluxo foi validado em formulário/simulador local, Chromium por CDP e
Chromium gerenciado com perfil persistente.
Não foi executado contra uma sessão autenticada do eproc TRF6. A seleção de
linhas usa a convenção Infra `input[id^='chkInfraItem']`; confirme-a no ambiente
real. Os seletores de minuta dependem de papel/texto porque não foram mapeados
nos anexos. Ausência ou ambiguidade gera pendência para conferência manual.

O sucesso de envio segue o critério do PRD: Intimar sai da página em até 15 s,
sem alertas de validação. Uma resposta ambígua é encaminhada à pessoa, sem
repetição automática. A data DCB pode ser removida na conferência; somente o
campo DCB que o próprio robô preencheu será limpo antes da reaplicação.

Não altera RMI, CID, início de incapacidade, trânsito em julgado, botões de
opção, prazos, parte ou evento vinculado sugerido. Não faz login, paginação,
OCR, análise de identidade ou chamadas a APIs do eproc.

## Arquivos

- `ceab/extractor.py`, `ceab/rules.py`: extração e regras puras.
- `ceab/state.py`: SQLite, comandos, aprovação e auditoria.
- `ceab/selectors.py`, `ceab/eproc.py`: seletores e automação por etapa.
- `ceab/worker.py`, `worker.py`: execução sequencial em navegador gerenciado
  ou, opcionalmente, por CDP.
- `ceab/browser_session.py`: abertura do Chrome com perfil persistente.
- `ceab/discovery.py`: descoberta local de navegadores e abas existentes.
- `ceab/launcher.py`, `app.py`: subprocesso e painel Streamlit.
- `ceab/demo.py`, `samples/`, `tests/`: simulador e validação local.

`selectors.py` fica dentro de `ceab/` para não ocultar o módulo `selectors` da
biblioteca padrão usado por subprocessos.

### Se Chrome do sistema não abrir

No Linux, o worker precisa rodar na mesma sessão gráfica do usuário. Um
Streamlit remoto na nuvem não abre uma janela no seu computador. Confira se
Chrome/Chromium está instalado. Se estiver usando o navegador fornecido pelo
Playwright, instale-o com `python -m playwright install chromium`.

Feche somente janelas que estiverem usando o perfil da automação antes de
reabrir. Não remova o perfil ou o banco como tentativa de corrigir falhas:
isso pode perder o login e as proteções contra reenvio.


## Nova tentativa após erro de leitura

Processos em **ERRO_LEITURA** ou **ERRO_PREENCHIMENTO**, sem envio ou tentativa
reservada, podem ser lidos novamente numa nova execução. Encerre ou cancele a
execução anterior, abra uma nova execução e inicie na lista do eproc. Registros
ignorados por erro também podem voltar à fila. Os dados e a aprovação antigos
são invalidados; a nova leitura exige nova conferência. O histórico permanece
no banco. Envios e tentativas já reservadas continuam bloqueados contra reenvio.
Não apague `runtime/state.db` para tentar novamente.

A leitura reconhece títulos quebrados em linhas e tabelas estruturadas com
Tipo, DIB/Restabelecimento e DIP mesmo quando o título é diferente. Se houver
mais de uma tabela compatível, exige revisão manual. HTML carregado por
JavaScript é lido do conteúdo renderizado no navegador; PDFs sem texto,
imagens digitalizadas e formatos sem esses campos exigem revisão manual.


## Conferência do PRD e mensagens de erro

Os nomes de arquivo dos dois PDFs enviados estão invertidos em relação à
seção 10 do PRD. O conteúdo de `exemplo1.pdf` é restabelecimento; o de
`exemplo2.pdf` é concessão. Os testes conferem os campos reais e também fazem
leitura pelo iframe, download do PDF e preenchimento com os textos completos
das opções do eproc, sem clicar em Intimar.

A verificação aceita o prefixo nativo `Expedida/certificada a intimação
eletrônica -` no evento e a repetição do nome no localizador (por exemplo,
`Expedir RPV - Expedir RPV`). Os valores continuam obrigatórios e os textos
precisam corresponder às formas completas conhecidas; nomes parecidos não
são aceitos.

`TIPO_DESCONHECIDO` para `JUD – IMPLANTAR BENEFICIO – ...` segue o PRD:
somente Concessão e Restabelecimento são suportados. `DATA_AUSENTE_OU_INVALIDA`
exige conferir DIB e DIP nas células de valor, sem buscar datas nas notas.
`DOCUMENTO_ILEGIVEL` com tabela ausente exige conferir a proposta escolhida.
O erro e o log agora identificam nome do documento, evento e sequência, sem
registrar links ou tokens de sessão. Não converter tipos nem inventar datas
para liberar o envio. Processos com falha continuam sem envio.
