# DEC-04D — terminal remoto

Os registros de D.1/D.2 abaixo são históricos. D.3 acrescenta interface VT e
consentimento no Tray; o gate operacional continua fechado conforme sua seção.

## DEC-04D.1 — sessões e transporte (registro histórico)

Base: `feature/rc46-agent-cleanup-closure`, HEAD inicial `49b984a`.
Ancestralidade de `901898c` (snapshot integrado RC46) e `acf65db` (DEC-04C.1)
confirmada; DEC-04C.2/3 presentes em `31452e2`/`49b984a`. Sem merge ou push.

## Transporte e autorização

WebSocket sobre TLS, handler ASGI no backend Django existente e
`ClientWebSocket` .NET no agente. Ambas as conexões são de saída; nenhuma
porta no endpoint. Sem dependências adicionadas. O transporte é uma task
independente no mesmo Worker Windows; não usa fila de jobs nem a trava de
inventário. Cadências anteriores preservadas.

Flags padrão false: `NIGHTOWL_TERMINAL_ENABLED`,
`NIGHTOWL_TERMINAL_SINGLE_PROCESS` e `remoteTerminalTransportEnabled` no agente.
A segunda flag declara **um único processo/event loop ASGI**. Cada sessão
fica vinculada ao UUID desse processo; worker diferente falha fechado.
Não existe relay distribuído nem retomada de sessão após restart.

`TerminalAccessGrant` vincula explicitamente operador, endpoint e validade.
Exige usuário técnico ativo + `agents.open_terminalsession` + grant ativo,
inclusive para superuser. Grants são provisionados pelo backend administrativo
via ORM nesta fundação, sem telas ou nova API de concessão.
Identidade, token do agente, lifecycle e capacidade recente
`remote_terminal_transport:1` são conferidos. Permissão/grant/identidade são
revalidados no handshake e durante a sessão (a cada ~1 s, mais tempo de DB).

Operador usa sessão Django com CSRF no HTTP e Origin exato no WebSocket.
Agente usa bearer existente + machine_id para obter seu ticket. Tickets
aleatórios de 256 bits, separados por papel, vinculados à sessão/identidade,
duram 30 s e são consumidos atomicamente uma vez. Somente hashes SHA-256
persistem; credencial segue no primeiro frame, nunca em URL, auditoria ou log.
Respostas de controle têm `Cache-Control: no-store, private`.

## Contrato v1 e limites

| Operação | Contrato |
|---|---|
| Solicitar/autorizar | POST `/api/agent/terminal/sessions/<endpoint>/`, `{"type":"request","protocol":1}` → 201 com ID, ticket, path WS, validade e modo; 403 rejeita autorização |
| Descoberta do agente | POST `/api/agent/terminal/poll/`, `{"machine_id":"..."}` → ticket agent ou `session:null` |
| Conectar | `/ws/terminal/<session>/<operator\|agent>/`, subprotocolo `nightowl-terminal.v1`; primeiro JSON `authenticate` com `credential` → `connected` |
| Pronta | Ambos conectados → `ready`, `mode:synthetic`, `execution:false` |
| Dados | Frame binário: canal de 1 byte + sequência uint64 big-endian + 1..16384 bytes; canais 0=entrada do operador, 1=stdout/2=stderr do agente; sequência crescente por remetente desde 1 |
| Resize | Operador: `resize` com `cols`/`rows` inteiros 1..500; agente: `resized`. Apenas confirmação sintética |
| Keepalive | Servidor `ping` com nonce; cliente `pong` com mesmo nonce |
| Encerrar | `close` no WS ou POST `/api/agent/terminal/cancel/<session>/`, `{"type":"cancel"}` → `closed`; ambos os lados são encerrados |
| Falha | `error`/`closed` com código fixo, incluindo timeout, revogação, frame inválido ou backpressure; nunca contém payload de terminal |

Máximo 4 sessões globais, 1 por endpoint e 1 por operador, 8 sockets incluindo
handshakes e 16 aberturas/janela de 1 s. Constraints únicas parciais + lock de
admissão PostgreSQL; relay
também limita admissão em memória. Vida máxima 300 s; auth 5 s; espera do outro
peer 15 s; ping 10 s; peer sem resposta 25 s. Operações de controle/send têm
orçamento de 2 s. Poll ocioso do agente 5..5,25 s, com backoff até ~30 s.

Limites por peer: 100 mensagens/janela de 1 s, JSON até 1 KiB, fila de 32
mensagens e 256 KiB, mais um frame em envio. Saturação fecha o par, sem
descartar/reordenar silenciosamente. Credenciais não podem reconectar; uma
nova sessão é necessária. O agente apenas ecoa bytes no canal sintético,
sem interpretar comandos. Nenhum processo é criado: não há terminal SYSTEM.
Auditoria guarda somente sessão, ator, endpoint, transições e códigos.

## Latência: projeção, não benchmark do terminal

Sejam **B** o RTT navegador↔backend, **A** o RTT backend↔agente, **Rin/Rout** os
custos do relay/TLS/escalonamento por sentido e **P** o tempo do programa até a primeira saída.
Em sessão pronta: entrega de entrada ≈ `(B+A)/2 + Rin`; primeira saída visível
≈ `B+A + Rin+Rout + P`, acrescida de buffering/renderização futuros. Não há polling
por tecla nem novo processo/job por comando. Um script terá a mesma parcela
de rede, mais upload/tamanho, execução e buffering do programa, ainda ausentes.

| Cenário hipotético | B / A usados como hipótese | Entrega: parcela de rede | Ida + primeira resposta: parcela de rede |
|---|---|---|---|
| LAN | 1..3 / 1..3 ms | 1..3 ms | 2..6 ms |
| Internet moderada | 20..50 / 40..100 ms | 30..75 ms | 60..150 ms |
| Instável | 50..150 / 100..500 ms, antes de perdas | 75..325 ms | 150..650 ms, podendo exceder muito com perdas |

São condições de cálculo, **não percentis observados nem SLAs**. Não há
amostras WAN/LAN para inferir p50/p95 reais. TCP head-of-line, perda, proxy
com buffering/idle timeout, CPU e congestionamento podem dominar a cauda.
O teste de loopback abaixo mede somente o transporte sintético.

Abertura inclui autorização HTTP (~B + DB), descoberta pelo poll e handshakes
dos dois peers. Com chegada aleatória e poll saudável de ~5 s, só a espera
do poll projeta p50 ≈2,5 s/p95 ≈4,8 s (hipótese uniforme). TCP + TLS 1.3 +
upgrade + autenticação acrescentam aproximadamente 3..4 RTTs por conexão
nova, podendo sobrepor-se entre peers. Não são medições de abertura. Sem
retomada automática: desconexão exige nova autorização/tickets e repete esse
custo; não reenvia bytes anteriores. Jobs tradicionais incluem descoberta,
fila, executor e persistência/ACK por execução; são inadequados para teclas.

Fluxo contínuo depende da banda útil: teto de payload por direção ~1,56 MiB/s
se cada uma das 100 mensagens/s tiver 16 KiB; não é throughput medido.
Máximo de payload enfileirado no relay: `4 × 2 × 256 KiB = 2 MiB`, além de
frames em envio, sockets, TLS, framework e DB. Agente usa buffer fixo de
~16 KiB + buffers WebSocket/TLS. Sem processos ConPTY neste consumo.
CPU cresce com bytes, masking/TLS e mensagens; não há fundamento para estimar
percentual. São 2 WS/sessão + revalidação DB por peer/s; endpoints habilitados
ociosos acrescentam ~0,2 requisição/s cada. Esse poll e a thread de controle
DB compartilhada são gargalos de escala, limitados aqui ao piloto pequeno.

DEC-04D.3/piloto: instrumentar por ID de sessão e sequência, sem conteúdo:
request→autorização, descoberta→auth, ambos conectados→ready, tecla→recepção
no agente, escrita ConPTY→primeiro byte→renderização, resize e fechamento.
Medir p50/p95 por cenário e tipo/tamanho de script, filas, bytes, erros,
reconexões, CPU/RAM/conexões. Usar relógios monotônicos locais; eco/ACK de
marcadores sintéticos para RTT ponta a ponta; correlacionar spans para evitar
subtrair relógios não sincronizados. Não instrumentado como produto em D.1.

## Validação e bloqueios

Build Release .NET afetado: zero avisos/erros. `--terminal-transport-only` e
23 testes Django de `agents.test_terminal_transport` passaram, incluindo
replay concorrente, expiração/revogação em sessão aberta, isolamento, limites,
backpressure e timeouts de auth, join, keepalive, envio e DB.
`git diff --check` passou. Loopback usa o ASGI
real e cliente .NET real, com adaptador RFC6455 **somente de teste**, sem TLS,
proxy, navegador, ConPTY ou execução real. Resultado final de 20 ecos:
p50 **0,366 ms**, p95 **0,451 ms**; amostra pequena, não extrapolável.

**Bloqueio para ativação real:** ambiente contém Gunicorn/WSGI e entrada ASGI,
mas não servidor ASGI com WebSocket (Uvicorn/Daphne/Channels indisponíveis).
Nenhuma infraestrutura/dependência foi instalada. O harness não é servidor
de produção. Homologação futura exige servidor ASGI, um processo/event loop,
TLS/proxy confiável com upgrade sem buffering, limite de mensagem 16393 bytes
e fila de recepção do servidor limitada; desativar compressão e logging de
frames/corpos. Não confiar em X-Forwarded-* arbitrário.
TLS e concorrência PostgreSQL não foram medidos/testados nesta etapa.
Tickets de 30 s, join de 15 s e relógios divergentes podem impedir abertura
em redes instáveis; não se reduzem as garantias de autorização para reconectar.
Após crash, sessões do owner anterior não retomam e expiram em até 300 s;
falha de DB fecha sockets, podendo deixar a conclusão persistida pendente
até expiração. Aviso preexistente de `staticfiles` ausente, sem impacto.

DEC-04D.2 ainda precisa implementar ConPTY sob identidade interativa **não
SYSTEM**, criação/encerramento seguro do processo, integração de I/O/resize
real e tratamento de falhas. Navegador e instrumentação E2E ficam para etapas
posteriores. Nenhuma dessas funcionalidades foi iniciada aqui.

Arquivos: `.env.example`; agente `AgentConfig.cs`, `AgentApiClient.cs`,
`RemoteTerminalTransport.cs`, `Worker.cs`; testes .NET `Program.cs`,
`TerminalTransportTests.cs`; backend `agents/models.py`, `urls.py`,
`terminal_sessions.py`, `terminal_transport.py`, migração `0042`; harness/testes
`terminal_test_harness.py`, `test_terminal_transport.py`; `config/asgi.py`,
`config/settings.py` e este documento. Sem novas dependências, suíte completa,
merge, push, deploy, publicação ou instalação em endpoints.

## DEC-04D.2 — motor ConPTY

Base preservada: `5467d8b`. Motor nativo com `CreatePseudoConsole`,
`STARTUPINFOEX`, `ResizePseudoConsole` e `ClosePseudoConsole`. Shell fechado:
somente Windows PowerShell 5.1 do System32, sem perfis nem ExecutionPolicy
bypass. Bootstrap estático configura UTF-8; nenhum comando do operador entra
na linha de criação. Após `ready`, entrada é enviada ao terminal autorizado.
Saída UTF-8/VT é incremental; stdout/stderr são combinados pelo ConPTY no
canal 1, como em um console real. Não é executor de jobs.

**Identidade e consentimento:** `terminalConPtyEnabled` false, SID autorizado
vazio e `terminalWindowsSessionId` ausente por padrão. Aceita apenas sessão
Windows explicitamente configurada, token interativo médio ou inferior,
sem elevação, Administradores habilitado ou privilégios administrativos
(inclusive presentes/desabilitados). SYSTEM/LocalService/NetworkService e
session 0 são rejeitados. O token do filho suspenso é verificado antes de
`ResumeThread` (SID, logon LUID, session, integridade e privilégios).

No serviço SYSTEM, usa `WTSQueryUserToken` **somente** para a sessão configurada,
token restrito via `CreateRestrictedToken` e `CreateProcessAsUserW`. Não enumera
usuários ativos, busca linked tokens, habilita privilégios ou tenta fallback
como SYSTEM. Desktop fixo `winsta0\default`, com ACLs existentes; ambiente
fechado sem herdar segredos/stdio do serviço. Quando o host já é o próprio
usuário autorizado não elevado, permite `CreateProcessW` sob esse mesmo token
validado. Este foi o caminho realmente testado: `g15-gabriel\gabri`, integridade
8192, Administradores deny-only, sem token elevado.

Exige recibo DPAPI CurrentUser por sessão, vinculado a ID remoto, machine_id,
operador, SID, logon LUID, sessão Windows, shell e expiração (máximo 5 min).
`ApproveCurrentUser(..., explicitlyConfirmed:true)` é a API de aprovação local;
**não é chamada pelo serviço/transporte**. Não há UI/CLI de aprovação neste
incremento. Recibo em `%LOCALAPPDATA%\NightOwl\TerminalConsent\<id>.bin`,
consumido atomicamente para `.used`, sem sobrescrever/replay. Ausência,
alteração ou remoção rejeita/encerra; revogação local verificada a cada ~1 s.
Arquivos de consentimento dos testes foram removidos ao término.

**Integração:** flag backend `NIGHTOWL_TERMINAL_CONPTY_ENABLED=false` e concessão
`allow_conpty=false` por padrão (migração aditiva `0043`). POST existente aceita
`mode:conpty`, exige grant/capacidade `remote_terminal_conpty:1`; padrão continua
synthetic. Ambos WS conectados → `transport_ready`; somente o agente pode
confirmar `process_ready` com `non_system:true`. Backend revalida autorização
e então envia `ready`; entrada anterior é rejeitada. Cliente só inicia o motor
nessa sessão e encerra-o em qualquer fechamento/erro/desconexão. Perda de
conexão nunca retoma processo nem reenvia entrada; exige nova sessão/consentimento.

**Posse/limites:** 1 motor por agente/endpoint, 4 sessões globais preservadas.
Job Object atribuído atomicamente na criação (`JOB_LIST`), root suspenso antes
da inspeção; kill-on-close, sem breakaway, até 8 processos, 256 MiB de commit
por processo e 512 MiB no job, CPU rate hard cap de 20% e prioridade abaixo da
normal. Fila nativa de até 16 blocos/256 KiB, 1 MiB de entrada e 16 MiB de saída por sessão,
backpressure/escrita com orçamento de 2 s, coalescência dos fragmentos já
disponíveis e pacing abaixo de 100 frames/s. Limites WS de D.1 mantidos. Kill do job e confirmação de zero
processos precedem a liberação; leitura permanece drenando durante fechamento
do HPCON para evitar deadlock. Falha de confirmação retém a posse, sem
autorizar sucessor. Heartbeat, inventário, telemetria e jobs não compartilham
essa trava e suas cadências não foram alteradas.

### Medições locais e validação D.2

Build Release afetado: zero avisos/erros; modos `--conpty-only` e
`--terminal-transport-only` passaram. Django: 32 testes focados de D.1/D.2
passaram, incluindo loopback ASGI real + cliente .NET + PowerShell real,
desconexão, negação de sessão/identidade, grant/replay/booleanos estritos,
Unicode/especiais, saída incremental antes do fim do comando, resize,
cancelamento/timeout, revogação local, filho encerrado, limites de entrada/saída e
consumidor bloqueado. `git diff --check` aprovado. Sem suíte completa.

| Medição | Resultado da amostra registrada |
|---|---|
| Comando→marcador executado, só motor, 100 comandos aquecidos | p50 13,492 ms; p95 16,616 ms |
| Mesmo tipo de comando via WS loopback, 100 comandos | p50 15,804 ms; p95 21,121 ms |
| Eco interativo sem executar a linha, só motor, 100 amostras | p50 15,478 ms; p95 16,019 ms |
| Eco interativo via WS, 100 amostras | p50 30,873 ms; p95 32,360 ms |
| CreatePseudoConsole, 3 sessões observadas | 8,248–10,464 ms; sem percentis por amostra insuficiente |
| Criação/verificação/resume do processo, mesmas 3 | 4,858–13,395 ms; não significa PowerShell pronto |
| Abertura→primeiro marcador PowerShell executado | 486,128 ms em uma sessão; 433,136 ms após retorno do motor; inclui primeiro parsing, sem percentis |
| WS do operador aceito→processo confirmado no loopback | 290,585 ms em uma sessão; exclui poll e prompt pronto |
| Blocos de saída no teste incremental | 476 blocos; intervalos 0,016–393,247 ms, incluindo pausa deliberada de 350 ms |
| Encerramento/liberação, 3 sessões | 1,999–37,286 ms; sem percentis |
| Sessão completa local: CPU acumulada do job | 2.437,5 ms para startup, 100 ecos, 100 comandos e testes auxiliares; não é percentual nem custo ocioso |
| Working set do PowerShell / pico de commit do job nessa sessão | 89,01 MiB / 78,22 MiB |

Marcadores são montados por expressão para excluir o eco da própria linha de
entrada. O primeiro byte pode ser VT/prompt, não saída de comando. As outras
sessões incluem cancelamento imediato/timeout. CPU inclui descendentes do job;
RAM indicada não inclui todo o custo do conhost, agente, backend ou TLS.
Sem cold-cache controlado; p50/p95 são descritivos das 100 amostras por cenário,
não intervalos de confiança ou SLA. Amostras de abertura/cleanup são insuficientes para percentis.
Não houve navegador, TLS/proxy real ou rede externa. D.1 mediu apenas eco
sintético (~0,366/0,451 ms), não execução: a diferença observada inclui parser,
renderização ConPTY e pacing. Não se subtraem percentis para inferir custo de
rede, nem se extrapola para internet/produção. Scripts longos continuam
dependendo de sua execução; não foram simulados como comandos instantâneos.

**Bloqueios/riscos e D.3:** serviço SYSTEM→WTS/token restrito não foi exercitado
nem instalado neste host; disponibilidade de privilégios, perfil e desktop
deve ser homologada. Sem SID/sessão/recibo local válidos, esse caminho fica
fechado. A ausência de servidor ASGI WebSocket de produção e a restrição de
um processo de D.1 permanecem. Job Object contém descendentes normais e limita
recursos; não é sandbox contra ações de um operador autorizado via serviços
externos/brokers do Windows. Não promete isolamento de todo efeito colateral
de comandos arbitrários. Políticas de scripts do Windows continuam vigentes.
D.3 precisa da superfície de consentimento/identidade e do cliente VT no
navegador, fluxo de aprovação antes de tickets expirarem, instrumentação E2E
e homologação TLS/proxy/piloto. Nenhuma dessas partes foi iniciada.

Arquivos D.2: `ConPtyTerminal.cs`, `TerminalIdentity.cs`,
`RemoteTerminalTransport.cs`, `AgentConfig.cs`, `Worker.cs`; testes .NET
`ConPtyTests.cs`/`Program.cs`; backend `models.py`, `terminal_sessions.py`,
`terminal_transport.py`, `0043_conpty_session_mode.py`, `test_conpty_sessions.py`;
`settings.py`, `.env.example` e este documento. Sem push/merge/deploy ou novas
dependências. Referências técnicas: [ConPTY](https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session),
[CreateProcessAsUser](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasusera).

## DEC-04D.3 — navegador e consentimento local

Base preservada: `5467d8b` e `7b53075`. A página existente do endpoint ganhou
painel VT, estados de autorização/consentimento/conexão/erro/encerramento,
identidade Windows confirmada e botão Encerrar. xterm.js 5.5.0 + addon-fit
0.10.0 são servidos localmente, com licenças MIT e integridade npm/arquivos
em `static/vendor/xterm/manifest.json`; não havia biblioteca VT reutilizável.
Nenhum código executável de CDN é carregado na página com terminal habilitado.
Sem novas ferramentas, SDKs ou infraestrutura instalados.

Fluxo: POST autorizado cria `awaiting_consent` (até 120 s), sem tickets.
O agente usa o controle D.1 já existente e projeta somente metadados locais,
sem bearer/ticket/conteúdo. Tray mostra operador, endpoint, origem, identidade,
sessão Windows e validade; Negar é o padrão, inclusive Enter/fechar diálogo.
Permitir emite recibo DPAPI CurrentUser; código de identidade/consentimento
foi compartilhado com o Tray, mantendo fachada/API do motor D.2.

A projeção exige ACL sem escrita de terceiros e sem reparse points. Serviço
usa origem SYSTEM/Administradores protegida; host do próprio usuário usa prova
DPAPI vinculada ao conteúdo e ao SID/sessão configurados. Não se enumera nem
seleciona usuário conectado. Recibo continua vinculado a operador, endpoint,
ID remoto, logon LUID e expiração. Remoção/alteração revoga; Tray revoga ao
sair/logoff e o serviço revalida o mesmo logon Windows durante a execução.
Os testes de ACL usam apenas DACL de fixture privada, sem SeSecurityPrivilege.

Após aprovação autenticada do agente, o operador obtém seu ticket por **POST
com CSRF** (`terminal/connect/<id>/`); GET de status nunca emite credencial.
São aceitas as duas ordens de conexão. `process_ready` deve comprovar a mesma
identidade aprovada antes de `ready`; entrada anterior continua rejeitada.
Negação, expiração e revogação não abrem processo. Migração `0044` inclui
consentimento/identidade e mantém unicidade também enquanto aguarda aprovação.
APIs internas D.2/sintéticas continuam exigindo seu controle local de identidade.

Bytes interativos usam exclusivamente WS. Consulta HTTP de estado só existe
durante a autorização/consentimento, não para transportar entrada/saída.
Fechar página usa cancelamento CSRF via beacon + fechamento WS. Reconexão exige
novo ID, tickets e consentimento; não há retomada/replay de entrada. Buffers
JS até 256 KiB, frames até 16 KiB, scrollback zero, geometria até 240×80;
OSC52/ativação de links não acionam clipboard/navegação. Tela/credenciais são
descartadas ao encerrar; nada é salvo em storage/transcrição/log. Limites de
D.1/D.2, tarefas independentes e cadências anteriores foram preservados.

### Validação e medições D.3

Builds Release de Shared/agente/testes/Tray: zero avisos/erros. Modos focados
`--terminal-consent-only`, `--terminal-transport-only`, `--conpty-only` passaram.
39 testes Django focados passaram, incluindo consentimento aprovado/negado/
expirado, isolamento, CSRF, ticket único, ordem agente-primeiro e D.1/D.2.
Smoke com Chrome já instalado e Node/CDP nativo: **browser, VT, backend e
ConPTY reais; clique de consentimento simulado pelo teste**, com recibo DPAPI
real e fixture privada. Validou Unicode/ANSI, resize, saída incremental,
encerramento manual, segunda sessão com novo consentimento e pagehide com
limpeza de processos. Não executou sessão corporativa nem clicou no Tray real.

| Rodada final isolada, sem TLS/proxy/internet | Medição |
|---|---|
| Digitação→eco visível, 100 amostras | p50 49,70 ms; p95 64,60 ms |
| Enter→marcador executado/renderizado, 100 amostras | p50 56,55 ms; p95 71,90 ms |
| Recepção→render aproximado, 771 blocos | p50 13,10 ms; p95 16,50 ms |
| Comando→marcador no WS sem navegador, 100 amostras | p50 15,595 ms; p95 19,154 ms |
| Solicitação→autorização | 55 ms, uma amostra; sem percentis |
| Consentimento→processo confirmado, segunda sessão | 874,504 ms, uma amostra; sem percentis |
| Descarte da tela / limpeza nativa de 2 sessões | 9,8 ms / 25,089 e 40,250 ms; sem percentis |
| Sessão principal: CPU acumulada do job / working set PS / pico commit job | 2.812,5 ms / 83,13 MiB / 66,79 MiB |

Instrumentação usa apenas IDs, durações e contagens. Eventos JS distinguem
recepção/render e entrada→próximo render (este último não prova causalidade de
um comando); o teste usa marcadores montados por expressão para excluir eco da
linha. Render usa callbacks VT + RAF; números visuais incluem automação/CDP e
poll de observação. Não são latência pura da rede nem SLA de pixels/hardware.
CPU/RAM não incluem todo Chrome/conhost/backend. Rodadas sob carga concorrente
apresentaram caudas maiores, inclusive sem navegador; a tabela registra rodada
sem testes paralelos. D.2 registrou ~15,8/21,1 ms no WS: a rodada sem browser
permaneceu próxima, enquanto a UI acrescenta renderização/automação. Não se
subtraem percentis nem se extrapola para TLS/internet/produção.

### Gate e encerramento do incremento

`NIGHTOWL_TERMINAL_BROWSER_ENABLED=false` por padrão. Além das flags D.1/D.2,
`terminalServiceConsentHomologated=false` bloqueia **antes de WTS** o caminho
SYSTEM→usuário; também impede anunciar a capacidade correspondente. Não foi
comprovado o fluxo com serviço SYSTEM + Tray real neste ambiente e ele não foi
habilitado. O fluxo isolado usou o próprio usuário médio, sem elevação.

ASGI de produção continua indisponível; o adaptador stdlib é só de teste. O
relay requer um único processo/event loop WS. HTTP sticky-cookie sozinho não
garante que operador e agente cheguem ao mesmo worker. Para múltiplos workers
seria necessário relay compartilhado com filas/buffers/limites/encerramento
globais e roteamento por sessão, mantendo consumo atômico de tickets e
revalidação; não foi instalado Redis/Channels ou criado outro serviço.
TLS/proxy requer upgrade sem buffering, limites WS, idle timeout compatível,
certificado confiável e forwarded headers apenas de proxy confiável.

RC46 Pilot ainda exige aprovação manual real no Tray, serviço com token
restrito/WTS, logoff real, ACLs de instalação, testes TLS/proxy/topologia e
medidas em condições representativas. **Implementação D.3 encerrada; DEC-04D
permanece bloqueada para habilitação operacional por esses gates.** Sem push,
merge, deploy, publicação, Fast Install ou novas ações administrativas.

## DEC-04D.4.1 — bloqueio de segurança

Não há modo administrativo não assistido implementado ou habilitado. O fluxo
existente exige consentimento e rejeita tokens administrativos e SYSTEM. Falta
um mecanismo de confiança do navegador na chave do agente que vincule a chave
à sessão; o relay atual não fornece essa autenticação independente. A entrega
de senha não foi adicionada ao backend, navegador ou agente.

O Job Object atual encerra os processos associados, mas um administrador pode
solicitar a serviços ou outros intermediários Windows que criem processos fora
do Job. Portanto, isso não comprova a garantia solicitada para todos os
processos criados indiretamente. É necessário definir uma fronteira de execução
que imponha essa garantia ou alterar explicitamente o requisito antes de
habilitar o terminal administrativo. Logout, lease e identidade administrativa
também necessitam implementação e validação em Windows real; nenhuma conta ou
privilégio da máquina foi alterado. DEC-04D.4.2 e o piloto continuam pendentes.

## DEC-04D.4.1A — autenticação isolada e gate de confiança

O enrollment existente identifica endpoints e usa credenciais do agente; as
chaves de release autenticam artefatos, não uma chave individual do endpoint
para o navegador. Nenhuma dessas âncoras fornece associação verificável
independente do backend. Uma chave recebida pela mesma sessão não resolve isso;
um backend capaz de alterar o JavaScript pode capturar a senha antes da
criptografia. É necessária uma âncora e distribuição de cliente autenticadas
por canal independente, com vínculo endpoint/chave, rotação e revogação.
Não foi criada PKI, identidade persistente ou envio de credenciais; o caminho
continua bloqueado, sem interface ou integração de autenticação administrativa.

O componente isolado `TerminalAdministrativeAuthentication` prepara LogonUser
para conta local ou domínio e exige token elevado, membro efetivo dos
administradores, integridade alta e SID diferente das contas de serviço.
Não eleva token nem inicia processo. A senha entra como buffer mutável, cuja
memória gerenciada e cópia nativa são zeradas ao sair; o token é descartado
em falha e pertence ao chamador em sucesso. Não guarda senha; isso não demonstra
proteção do fluxo completo.

Build Release de agente/Shared/testes passou sem avisos. Testes focados
rejeitaram conta inexistente, SID SYSTEM e tokens sintéticos sem os privilégios
exigidos, e verificaram limpeza do buffer; consentimento existente também
passou. Seis testes Django do contrato existente passaram. Não houve login
administrativo real, teste de domínio, replay criptográfico, associação de
chaves ou teste de token válido; não existe ainda canal de credenciais para
testar. Para D.4.1B permanecem a confiança independente, identidade do agente,
transporte autenticado e anti-replay, expiração/revogação, autenticação real
homologada e a fronteira de execução/encerramento documentada acima.

## DEC-04D.4.1B — revisão da decisão de confiança

A Controlsul definiu o backend como confiável para intermediar credenciais
temporariamente. A autenticação da chave do agente independente do backend,
PKI adicional e criptografia ponta a ponta não são requisitos desta versão;
as afirmações anteriores sobre esse gate ficam supersedidas por essa decisão.
HTTPS/WSS válido, autorização por operador e endpoint, não persistência e
descarte da credencial, tickets únicos e revogação continuam necessários.

A garantia anterior de eliminar qualquer processo criado indiretamente por
um administrador também foi revisada: o encerramento deve cobrir processos
controlados pela sessão; alterações administrativas externas podem permanecer.
O Job Object existente é relevante para essa fronteira, mas o caminho
administrativo ainda precisa ser implementado e verificado.

O código atual não implementa o novo fluxo: `TerminalOperatorView` solicita
consentimento em modo ConPTY, `TerminalUserIdentity.ValidateFacts` rejeita
tokens elevados, `ConPtyTerminal` cria token restrito e o relay aceita apenas
eventos previstos e usa fila para envio. `TerminalAdministrativeAuthentication`
permanece isolado, sem transporte de senha ou ligação ao ConPTY. São lacunas
de implementação, não evidência de impossibilidade técnica no Windows.
Nenhum modo administrativo foi habilitado. Permanecem implementação e testes
de transporte sem persistência ou fila de senha, logout/revogação, validação
efetiva do token e ciclo de vida; não há homologação Windows administrativa.

## RC46-S01 — autenticação administrativa sem execução

Decisão vigente: backend confiável, HTTPS/WSS válido e credencial transitória;
não se exige PKI ou chave de agente independente do backend. Sessões `admin_auth`
usam permissão NightOwl, grant por operador/endpoint com `allow_conpty`, flags
e capability do agente, tickets únicos, expiração, Origin, CSRF e limites do
transporte existente. O modo não usa consentimento Tray nem inicia ConPTY.
O modo assistido anterior é preservado enquanto houver consumidores. A flag
do backend `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED` e a configuração do agente
`terminalAdministrativeAuthenticationEnabled` são desabilitadas por padrão.

O formulário envia usuário, domínio e senha após ambos os peers autenticarem
o WebSocket; a sessão aceita somente uma tentativa. O relay mantém a mensagem
transitoriamente em sua fila limitada em memória, sem armazenamento persistente,
e não registra conteúdo. O agente usa LogonUser e valida token administrativo
elevado, rejeita contas de serviço e descarta o token, sem criar processo.
Buffers mutáveis do agente e o campo do navegador são limpos; strings imutáveis
do navegador/backend e cópias do transporte não podem ser zeradas com garantia.
O backend confiável pode ler as credenciais durante a transmissão.

Teste integrado em loopback: navegador simulado por cliente WebSocket, backend
ASGI real, agente .NET real e LogonUser real rejeitando conta inexistente.
Segunda tentativa na sessão foi rejeitada; senha sintética não apareceu nas
linhas de sessão/auditoria nem na saída do agente. Validação de privilégios usa
tokens sintéticos; sem credenciais administrativas autorizadas, não foi
testado sucesso Windows real ou domínio, nem TLS real. Os testes de transporte
anteriores foram preservados. S02 precisa integrar o token ao ConPTY, definir
e testar cleanup de processos controlados e revogação/logout, além de validar
conta administrativa real e TLS antes da homologação. Não há terminal
administrativo executável nesta etapa.
