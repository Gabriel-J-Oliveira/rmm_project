# DEC-04D.1 — sessões e transporte (somente sintético)

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
