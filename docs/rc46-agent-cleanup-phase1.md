# RC46 - Lote 1: reconciliacao da auditoria e limpeza segura

Data: 2026-10-30. **RC46 continua NO-GO.** Somente esta primeira rodada foi
executada. Nenhum commit, push, merge, deploy, release, update ou acesso a endpoint
corporativo foi realizado.

## 1. Baseline e preservacao

- Worktree: `C:\Users\gabriel.oliveira\.codex\worktrees\rc46-agent-core\rmm_project`.
- Branch: `feature/rc46-agent-core`.
- HEAD: `0d5d8ceb28928b6a580cdff15a5144b373093296`.
- Worktree ja suja: DEC-01/02/03/04A/04B presentes, incluindo
  `agents/automation.py`, modelos, interfaces e `0039_job_automations.py`.
- Auditoria lida, nao alterada:
  `C:\Users\gabriel.oliveira\Downloads\rmm_project\docs\rc46-agent-cleanup-audit.md`.
  Esse documento registra explicitamente outro checkout: branch
  `codex/agent-telemetry-management`, HEAD `07812d3`.
- Hashes SHA-256 dos arquivos locais modificados/novos foram registrados antes
  da limpeza e comparados ao final para isolar este lote das DECs preexistentes.
- Antes de editar codigo produtivo: build Release OK, Shared OK, Windows completo
  com a falha conhecida de repair, hardware/heartbeat focados OK, telemetria
  focada OK. A referencia Django anterior era 713 testes/31 skips e a rodada
  focada DEC-04B era 48/1 skip; nao se afirma que foram repetidas antes da limpeza.

## 2. Divergencias da auditoria anterior

| Conclusao anterior | Implementacao RC46 verificada | Decisao |
| --- | --- | --- |
| BuildHeartbeat chama GetSystem/GetHardware e dois PowerShell | Nao ocorre: BuildHeartbeat usa identidade nativa, rede local, uptime e versoes de arquivos | Auditoria de outra copia, sem regressao CIM comprovada |
| CollectPayload repete OS/ComputerSystem/BIOS em dois scripts | ReadPlatform alimenta MapSystem e GetHardware; uma consulta por classe no script consolidado | Preservar DEC-02 |
| Bateria consultada no core e no enriquecimento | Win32_Battery somente em GetAssetHardwareDetails; root/wmi condicionado a uma bateria | Preservar, nao remover dados |
| Worker executa heartbeat, inventario e jobs no mesmo loop | Heartbeat independente; JobRuntime com dispatcher/executor/uploader; gate pesado compartilhado com inventario | Nao recentralizar |
| Telemetria principal a cada 300s | Modo continuo: nativo 10s, resumo/envio 60s; pipeline legado ainda existe | Nao eliminar modo legado |
| DEC-04B nao incorporada | Scheduler Django, tres modelos e migration0039 presentes | Preservar integralmente |
| jobsSeconds determina a consulta atual | JobRuntime usa jobSignalSeconds (2s default), jobsSeconds permanece persistido e consumido por legado/instaladores | Documentar legado, nao remover campo |
| Falta instrumentacao de coletores | CollectSection ja registra inicio, fim, duracao e resumo; contador PowerShellStarted ja existe | Reutilizar, nao duplicar logs |
| PowerShell legado pode ser removido | Instaladores/fallback e consumidores externos ainda existem | Nao remover nem desabilitar |
| Hardware/volumes/rede misturados | Planos basico/detalhado/volumes/rede/ancilar e telemetria estao separados | Preservar cadencias e contratos |

## 3. Heartbeat: caminho efetivo

`Worker.ExecuteAsync -> RunHeartbeatLoopAsync -> SendHeartbeatAsync ->
WindowsInventoryCollector.BuildHeartbeat -> AgentApiClient.PostHeartbeatAsync`.

Dentro de BuildHeartbeat:

- IPGlobalProperties.GetIPGlobalProperties: dominio local; sem DNS remoto explicito.
- NetworkInterface.GetAllNetworkInterfaces/GetIPProperties: IPv4 locais ativos.
- Environment.MachineName e TickCount64: hostname e uptime.
- GetFileVersionDetails -> File.Exists/FileVersionInfo/NormalizeFunctionalVersion:
  versoes de Tray/Updater, sem executar esses programas.
- BuildAgentMetadata: dicionario de metadados operacionais e caminhos.

SendHeartbeatAsync acrescenta capabilities e relatorio de configuracao de
telemetria ja efetiva; nao chama o coletor dinamico nem o inventario. Depois usa
HTTP autenticado e registra envio/falha. Checkpoints usam Interlocked; persistencia
de AgentState ocorre no loop de inventario, sem tornar o heartbeat uma coleta.

Nao ha chamadas explicitas CIM/WMI/PowerShell nesse caminho, inclusive nos helpers
examinados. O contador de criacao de PowerShell nao aumentou em 50 heartbeats reais
locais. Isso nao e tracing de todas as chamadas internas do Windows ou benchmark
de um servico instalado na frota.

Campos essenciais: machine_id/agent_id, hostname, dominio/FQDN quando disponivel,
versao, modo, timestamp/presenca e uptime. IPs e versoes dos auxiliares sao contexto
operacional barato preservado. Hardware profundo, OS/BIOS, modulos RAM, bateria e
usuario interativo nao pertencem ao heartbeat. Nao inferir usuario pelo SYSTEM.

As consultas OS/ComputerSystem/BIOS continuam no inventario ReadPlatform. Quando
basico e detalhado sao devidos juntos, o mesmo resultado e compartilhado. Metodos
publicos GetSystem/GetHardware continuam disponiveis: nao sao considerados mortos
apenas porque o Worker agregado nao os chama separadamente.

## 4. Mudancas efetivas deste lote

Unico arquivo funcional alterado:
`NightOwl.Agent.Windows/Collectors/WindowsInventoryCollector.cs`.

### Removido

- Construcao de CPU/baseboard/TPM/BIOS detalhados e posterior filtragem por
  Keys.Except/ToArray quando o plano solicita apenas hardware basico.
- Segunda normalizacao identica de discos fisicos quando MergeHardware ja a fez.
- `using System.Security.Principal` sem consumidor nesse arquivo.
- Nenhum metodo publico, campo persistido, consulta de inventario, alias de payload
  ou mecanismo de recuperacao foi removido.

### Consolidado

- GetBasicHardware produz diretamente as mesmas seis chaves: manufacturer, model,
  serial_number, memory_total_bytes, total_memory_bytes e collected_at. Hardware
  detalhado continua usando GetHardware; nao foi reclassificado como dinamico.
- MergeHardwareSections centraliza somente a composicao existente: enriquecimento
  disponivel delega ao normalizador completo; sem enriquecimento, storage valido
  ainda pode produzir discos. Falhas e dados parciais mantem a forma anterior.

Antes, em pseudocodigo:

```csharp
hardware = GetHardware(platform);
if (!plan.Detailed) RemoveEverythingExceptSixBasicKeys(hardware);
if (details.Count > 0) hardware = MergeHardware(hardware, details, storage, drive);
if (storageSucceeded) hardware["physical_disks"] = PhysicalDisks(storage, drive);
```

Depois:

```csharp
hardware = plan.Detailed ? GetHardware(platform) : GetBasicHardware(platform);
hardware = MergeHardwareSections(hardware, details, storage, drive);
```

Nenhuma consulta CIM adicional foi adicionada ou removida; as duas economias sao
de processamento/alocacao .NET, nao de acesso ao hardware. Gates de secao,
detailed_collected_at e estados/retencao de ultimo valor valido nao mudaram.

### Mantido

Worker, timers, CancellationTokens/shutdown, DI, jobs, filas/ACK, exclusividade,
schema/configuracoes, identidade, ACL, assinatura, update/repair/rollback e legacy.
NightOwl.Agent.Shared foi auditado/testado, mas nao alterado neste lote.

As cadencias permanecem: basico/rede diario, detalhado semanal, volumes 1h,
ancilar conforme collectSeconds; heartbeat 300s default, signal 2s default;
telemetria continua nativa 10s/resumo 60s/envio independente. Nenhum novo loop,
servico, cache global ou framework foi introduzido.

Estados reais usam available/failed/unknown em diversas secoes, e not_present
quando suportado (ex.: bateria). Nao foram renomeados para ok/error. Fabricante,
modelo, serial, UUID quando coletado pelo contrato existente, Part Number,
frequencias, bateria e DNS nao foram descartados nem sintetizados.

## 5. Inventario de configuracoes

Nenhuma propriedade JSON foi removida, migrada ou teve default alterado neste lote.
Classificacao abaixo separa uso atual, compatibilidade e pontos incertos; nao
constitui autorizacao de remocao.

| Parametro | Classificacao e consumidor |
| --- | --- |
| agentToken | Ativo necessario, autentica AgentApiClient; nunca impresso nas medicoes |
| machineId | Ativo necessario, identidade, filas e payloads |
| agentVersion | Ativo necessario, resolvido pela assembly em ConfigService |
| configMigrationVersion | Legado/ativo necessario, migrations de configuracao ate versao6 |
| serverBaseUrl | Ativo necessario, base de endpoints e validacao de origem v2 |
| heartbeatUrl | Ativo necessario, PostHeartbeatAsync |
| collectUrl | Ativo necessario, PostCollectionAsync |
| jobsPullUrl | Ativo necessario, fila autoritativa |
| jobsResultUrl | Ativo necessario, resultados e recibos |
| jobSignalSeconds | Ativo necessario, JobRuntime: clamp2..30, default2 |
| intervals.heartbeatSeconds | Ativo necessario, Worker, default300, minimo operacional5 |
| intervals.collectSeconds | Ativo necessario, cadencia ancilar/retry; nao controla sozinho o inventario modular, default3600 |
| intervals.jobsSeconds | Legado ainda necessario/publico; sem consumidor temporal no JobRuntime v2; instaladores/PowerShell ainda escrevem/leem |
| telemetryEnabled | Ativo necessario, habilitacao e recuperacao de configuracao |
| telemetryMode | Ativo necessario, seleciona continuous ou legacy; config antiga sem modo permanece legacy |
| telemetryWindowMaxBytes | Ativo necessario, fila continua; default128MiB, limite1..512MiB |
| telemetryWindowMaxAgeHours | Ativo necessario, fila continua; default/limite192h |
| telemetrySampleSeconds | Legado ainda necessario e contrato configure_telemetry; nao altera os 10s nativos fixos do modo continuo |
| telemetryFlushSeconds | Legado ainda necessario e contrato configure_telemetry; nao substitui o envio continuo60s |
| telemetryBufferMaxSamples | Legado ainda necessario, TelemetryBuffer, max2304 |
| telemetryBufferMaxAgeHours | Legado ainda necessario, buffer antigo, max192h |
| telemetryUrl | Ativo no legacy; modo continuo deriva control/live/windows de serverBaseUrl; possivel duplicidade de configuracao, nao removida |
| logPath | Ativo necessario, logger/diagnostico |
| statePath | Ativo necessario, checkpoints/config recovery e raiz de filas/catalogo |
| pendingResultsPath | Ativo necessario, resultados e compatibilidade de runners |
| jobsPath | Ativo necessario, JobStore/journal |
| installPath | Ativo necessario, runtime/versoes e lifecycle |
| packagesPath | Legado/reservado: normalizado/criado por ConfigService e escrito por instaladores; executor novo usa staging proprio; remocao incerta |
| cachePath | Legado/reservado: normalizado/criado e contrato dos instaladores; sem consumo de cache quente comprovado; remocao incerta |
| allowedJobTypes | Ativo necessario, allowlist/policy/capabilities; preservar remocoes explicitas |
| softwareInstallEnabled | Ativo necessario, feature flag interna defaultfalse |
| softwareCatalogSha256 | Ativo quando a feature e aprovada; pin independente, nao remover |
| MachineIdSource / HasValidToken | Computados/JsonIgnore, operacionais, nao campos persistidos |
| NIGHTOWL_HOME | Ambiente ativo, NightOwlPaths; nao modificar diretorios/ACL |
| NIGHTOWL_AGENT_CONFIG | Ambiente ativo, caminho de configuracao suportado |
| NIGHTOWL_TEST_SERVER | Exclusivo do harness loopback no cliente v2; nao ativado em producao nem removido aqui |
| WinswPath | Nao e AgentConfig .NET; parametro legado de instaladores, fora do lote |
| KeepScheduledTaskFallback | Parametro legado de instalacao, nao desabilitado |

AgentState preservado: machine_id/machineId, lastHeartbeatAt, lastCollectionAt,
lastBasicInventoryAt, lastDetailedHardwareAt, lastVolumesAt, lastNetworkInventoryAt,
lastAncillaryInventoryAt, lastJobPullAt, recentJobIds, install_status, installed_at,
uninstalled_at e extension data. Mesmo campo sem leitor quente atual pode ser
consumido por instaladores, Tray ou recuperacao: nao e apagado automaticamente.

## 6. Medicoes antes/depois

Mesmo harness e processo Release por rodada; JIT aquecido antes de cada medicao.
Normalizacao usa fixtures sinteticas, nao inventario de equipamento corporativo.
Antes usa a composicao antiga preservada no teste; depois usa delegates para os
helpers produtivos e verifica sua utilizacao no caminho agregado.

| Medida | Antes | Depois |
| --- | ---: | ---: |
| Mapeamento basico: alocacao gerenciada/chamada | 6328 bytes | 496 bytes |
| Mapeamento basico: tempo total2000 chamadas | 23,4769ms | 1,7221ms |
| Composicao hardware: alocacao/chamada | 17560 bytes | 11312 bytes |
| Composicao hardware: tempo total2000 chamadas | 97,765ms | 35,263ms |
| Enumeracoes da fonte de discos por composicao valida | 2 | 1 |
| Heartbeat nativo: p50, 50 chamadas | 12,5461ms | 5,4276ms |
| Heartbeat nativo: p95 | 17,5731ms | 6,4484ms |
| Heartbeat: alocacao/chamada | 23584 bytes | 23584 bytes |
| Heartbeat: processos PowerShell | 0 | 0 |
| Heartbeat: tamanho serializado do payload de teste | 1225 bytes | 1225 bytes |

Economia deterministica nesta fixture: 5832 bytes/chamada basica (92,16%) e
6248 bytes/composicao detalhada (35,58%). Nao representa reducao percentual da RAM
do servico/frota. Os tempos sao microbenchmarks locais de uma rodada, nao SLA.
Reexecucao final confirmou496/11312 bytes e uma normalizacao; os tempos totais
foram1,3941/32,9141ms, reforcando que latencia varia mesmo com alocacao identica.
O heartbeat NAO foi otimizado: a variacao temporal deve ser tratada como ruido,
aquecimento/cache do SO ou carga local, nao como ganho causado por esta limpeza.

O teste de telemetria existente mediu antes/depois71/56ms, sample1152 bytes e
batch de seis6983 bytes em ambas as rodadas; esse caminho tambem nao foi alterado
e a variacao nao e atribuida ao lote. Nao houve medicao de RSS sustentado, CPU da
frota, duracao completa CIM em hardware real ou volume de logs do servico instalado.
Os logs de secao existentes foram preservados, nao duplicados pelo benchmark.

## 7. Testes e reproducao

| Teste | Antes | Depois |
| --- | --- | --- |
| Build Windows Release | PASS, 0 avisos/erros | PASS, 0 avisos/erros |
| Shared | PASS | PASS |
| Windows completo | FAIL, repair runner conhecido | Mesma falha, grupos anteriores passaram |
| Hardware/heartbeat focados | PASS | PASS |
| Telemetria focada (10 grupos) | PASS | PASS |
| Jobs/continua/configuracao/handshake | Passaram ate o bloqueio no completo | Passaram ate o mesmo bloqueio |
| Contrato novo de equivalencia e contagem | PASS referencia anterior | PASS codigo produtivo |
| PowerShell5.1, scripts embarcados com CIM simulado | Referencia DEC-02 preservada | PASS, sem consulta real ao dominio |
| DEC-01/02/03/04A/04B Django e protocolo .NET local | Referencia anterior713/31 skips | PASS83 testes/2 skips nesta rodada focada |
| Configuracao/serializacao | Migrations existentes e fixture legada preservadas | Roundtrip de todo AgentConfig e campos explicitos preservado |
| git diff --check | Baseline sem conflito observado | PASS final |

Os dois skips Django sao navegador opt-in e concorrencia PostgreSQL; nao foram
tratados como aprovados. O teste calendario -> agente .NET -> recibo exato passou
com fixtures locais. Isso NAO comprova o anuncio de capability do Worker real.

O teste novo executa equivalencia de JSON para hardware basico completo/ausente e
merge com enriquecimento presente/ausente e storage sucesso/falha. Inclui RAM,
Part Number, ausencia de bateria, associacao de disco e aliases, sem perder as
validacoes anteriores de desktop/notebook/VM e timeout/cancelamento.

```powershell
dotnet build NightOwl.Agent.Windows.Tests -c Release --no-restore
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --cleanup-baseline
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --cleanup-only
dotnet run --project NightOwl.Agent.Shared.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --hardware-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --telemetry-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-HardwareInventoryCadence.ps1
git diff --check
```

Nao compilar enquanto testes integrados estiverem usando essas DLLs. O modo
--cleanup-baseline preserva a composicao antiga como referencia, mas nao e outra
implementacao de runtime: existe apenas no projeto de testes.

## 8. Arquivos deste lote

1. `NightOwl.Agent.Windows/Collectors/WindowsInventoryCollector.cs`: duas limpezas
   localizadas e import sem uso removido.
2. `NightOwl.Agent.Windows.Tests/Program.cs`: entradas --cleanup-baseline/--cleanup-only.
3. `NightOwl.Agent.Windows.Tests/CleanupPhase1Tests.cs`: regressao e medicoes.
4. `docs/rc46-agent-cleanup-phase1.md`: reconciliacao e entrega.

Comparacao de hashes do baseline: somente os dois arquivos preexistentes acima
mudaram, mais os dois arquivos novos deste lote. Backend, frontend, migration0039,
Shared, modelos/configuracoes, scripts, Tray, Updater, Uninstaller, Diagnostics e
todos os outros arquivos locais das DECs permanecem com os hashes anteriores.
O repositorio em Downloads foi apenas lido; nenhum artefato foi copiado para ele.

## 9. Riscos, pendencias e NO-GO

Risco funcional principal: divergencia entre o antigo filtro de hardware basico
e o mapper direto, ou alteracao da forma de dados em falha de enriquecimento.
Equivalencia de campos/aliases/JSON, mocks CIM e testes anteriores cobrem esses
caminhos. Nao se afirma homologacao de toda matriz fisica/VM/RAID em frota.

**Pendencia nova e bloqueante encontrada, nao corrigida neste lote:**
Worker.SendHeartbeatAsync anuncia `job_protocol_v2`, mas
agents.automation.known_v2 procura `jobs_protocol_v2`. Os fixtures de agendamento
usam o segundo literal. Assim, os testes sinteticos de calendario nao comprovam
que o heartbeat do Worker permita agendar no agente real. Nao ha execucao insegura:
o resultado esperado e bloqueio fail-closed por capability nao confirmada.
Correcao/alias retrocompativel e teste Worker -> heartbeat persistido -> scheduler
precisam de aprovacao especifica. Nao mudar o contrato silenciosamente na limpeza.

Outras pendencias preservadas:

- `TestRepairRunnerScriptPersistsCompletedResult`: Repair runner pending result
  should be completed. Reproduzido antes e depois, sem modificar o teste/runner.
- Migrations, locking e concorrencia reais PostgreSQL nao homologados neste host.
- Gates anteriores de carga, reconexao/TLS, lifecycle em VM, MSI assinado e DEC-03.
- Revisao futura de jobsSeconds, telemetryUrl custom no modo continuo,
  packagesPath/cachePath e compatibilidade de caminhos; nao remover dados agora.
- Inventario real da populacao PowerShell/servicos/tarefas antes de aposentadoria.
- Logs, diagnosticos e filas podem ter oportunidades de otimizacao, mas ownership,
  limites, retencao e recuperacao nao foram simplificados por aparencia.

Nao ha segundo lote autorizado/executado, limpeza de ProgramData, alteracao de
enrollment/identidade/ACL, remocao de tarefa ou mudanca em assinatura/rollback.
Para desfazer somente esta limpeza, revisar os hunks listados contra o baseline,
preservando todas as DECs; nao usar reset/checkout do arquivo inteiro contra HEAD.

**Resultado:** Lote1 local validado nos limites descritos. RC46 permanece NO-GO
para publicacao, inclusive pela divergencia de capability descoberta.
