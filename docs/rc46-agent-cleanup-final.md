# RC46 - Consolidacao final do agente Windows

Data: 2026-10-30. Resultado: **SIMPLIFICACAO PARCIAL / NO-GO para publicacao**.

## 1. Escopo e baseline

Worktree: `C:\Users\gabriel.oliveira\.codex\worktrees\rc46-agent-core\rmm_project`.
Branch: `feature/rc46-agent-core`. HEAD: `0d5d8ceb28928b6a580cdff15a5144b373093296`.
O HEAD nao representa sozinho a implementacao: as DEC e os lotes anteriores estao na worktree suja e foram preservados.

Fontes: codigo atual, `rc46-agent-cleanup-phase1.md`, `rc46-jobs-v2-capabilities-fix.md` e relatorios DEC-01/02/03/04A/04B desta pasta. A auditoria antiga de outra branch nao foi tratada como descricao do codigo atual.

Baseline executado antes das alteracoes: builds dos dez projetos .NET, testes Windows, Shared, Updater e Uninstaller. O primeiro build sem restore encontrou assets ausentes em Diagnostics; o build com restore concluiu. A suite Windows ja falhava em `TestRepairRunnerScriptPersistsCompletedResult`: `Repair runner pending result should be completed.` A falha permanece identificada, sem skip ou relaxamento.

Preservados: heartbeat sem CIM/WMI/PowerShell; inventario modular; telemetria continua e ACK persistido; loops independentes de jobs; automacoes; coleta de servicos e instalacao protegida de software. O Worker continua anunciando `jobs_protocol_v2` e o alias `job_protocol_v2`; o backend aceita os dois nomes exatos, sem promover capacidades desconhecidas.

Nao houve commit, push, merge, deploy, publicacao, acesso a producao, atualizacao de endpoints ou inicio de DEC-04C/04D.

## 2. Alteracoes por lote

### Lote 2 - Configuracao

- Removida a segunda normalizacao da allowlist em `ConfigService.Normalize`: `Load` ja chama `ApplyConfigMigrations`, que continua normalizando inclusive para consumidores diretos. Ordem, deduplicacao, remocoes explicitas e versoes futuras permanecem cobertas.
- Limites de intervalos legados centralizados em constantes de `TelemetrySettings`, usadas tanto pelo clamp da configuracao quanto pela validacao estrita. Clamp e rejeicao continuam comportamentos diferentes, intencionalmente.
- Nenhuma configuracao publica, alias de estado, URL ou cadencia foi removida.
- Testes novos: `ConfigurationConsolidationTests.cs`, executaveis por `--configuration-consolidation-only`.

### Lote 3 - PowerShell legado

Auditoria e estrategia de aposentadoria; nenhum componente legado removido ou desabilitado. Nao foi demonstrada ausencia de consumidores na frota.

### Lote 4 - Lifecycle e Tray

- `AgentLocalState.Load` deixou de executar bootstrap/migracao de caminhos a cada refresh. O bootstrap permanece uma vez em `Program.Main`; atualizacao da UI passa a ser somente leitura.
- Tray aceita servico existente em inicializacao/parada temporaria por ate dois minutos, medidos por relogio monotonico. Running limpa a tolerancia; mudancas entre estados nao-running nao prolongam o limite. Servico ausente ou consulta inacessivel continua encerrando o Tray.
- Suprimido log repetitivo de estado Running a cada 30 segundos; transicoes continuam registradas.
- `ServiceController` e descartado tambem quando sua consulta falha.
- Nao ha start/stop/restart automatico do servico, novo residente ou redesenho da interface.

### Lote 5 - Footprint

- `LogTailReader` compartilhado le no maximo 1 MiB mais um byte de fronteira e retem ate 400 linhas. Snapshot do tamanho impede crescimento infinito durante append. Permite compartilhamento de leitura/escrita/exclusao, trata BOM, UTF-8, CRLF e primeira linha parcial.
- Tray usa o leitor limitado; nao cria cache duplicado. Estado persistido continua sendo fallback para heartbeat fora da janela. Arquivo com linha gigantesca nao garante recuperar 400 linhas; leitura truncada e explicita no retorno do helper.
- Fixtures exercitam o leitor real, append, rotacao/truncamento, linhas malformadas, estado persistido, leitura sem criar arquivos e politica de reconexao.
- Diagnostics ganhou fixture isolada com sanitizacao sentinela e verificacao do manifesto. O executavel nao foi alterado.

## 3. Remocoes e consolidacoes

Codigo removido: normalizacao redundante da allowlist; leitura integral de log no Tray; bootstrap de caminhos por refresh; logging continuo de Running sem transicao. Nenhum executavel, pacote NuGet, formato publico ou controle criptografico removido.

Codigo consolidado: limites de telemetria e leitura limitada de logs. A pequena politica de observacao do servico permite teste deterministico sem iniciar SCM ou interface grafica.

Configuracoes eliminadas: **nenhuma**. Caminhos reservados e parametros legados permanecem por compatibilidade; nao sao prova de codigo morto.

## 4. Matriz de configuracao

Origem geral: `AgentConfig` define defaults novos; instalador/config local os persistem; `ConfigService.Load` normaliza e migra. Consumidores abaixo referem-se ao codigo atual, nao a uma release ja publicada. Decisao de todas as linhas: preservar, salvo consolidacao interna explicitada.

| Nome | Origem/default | Consumidor e uso real | Compatibilidade/decisao |
|---|---|---|---|
| agentToken | Config/instalador; vazio | AgentApiClient, autenticacao | Preservar segredo e validacao de placeholder; nao registrar |
| machineId | Identidade/config; vazio | API, estado e jobs | Nao alterar identidade nem precedencia de migracao |
| agentVersion | Modelo 0.1.0.7; runtime usa assembly | Heartbeat/capabilities | Nao mudar versao nem produzir RC |
| configMigrationVersion | Modelo 1; migracoes atuais ate 6 | ConfigService | Preservar configuracao futura e migracao existente |
| serverBaseUrl | Config; vazio/derivado | API, telemetria continua/live/control | Manter validacoes existentes |
| heartbeatUrl | Config ou sintese da base | Heartbeat | Preservar override e endpoint publico |
| collectUrl | Config ou sintese da base | Inventario | Preservar |
| jobsPullUrl | Config ou sintese da base | Jobs Pull | Preservar |
| jobsResultUrl | Config ou sintese da base | Results/ACK | Preservar |
| telemetryUrl | Config/sintese | Pipeline legado | Continua deriva rotas da base; nao trocar silenciosamente override |
| intervals.heartbeatSeconds | Modelo 300 | Loop leve independente | Preservar |
| intervals.collectSeconds | Modelo 3600 | Inventario auxiliar/retry | Nao controla mais toda coleta modular; preservar consumidores legados |
| intervals.jobsSeconds | Modelo 45; instaladores podem gravar outro valor | Polling legado | V2 usa seu protocolo; nao remover campo publico |
| jobSignalSeconds | Modelo 2, clamp 2..30 | Comunicacao rapida V2 | Preservar |
| telemetryEnabled | Novo true; ausencia em config antiga false | Pipelines | Nao habilitar implicitamente legado |
| telemetryMode | Novo continuous; ausencia antiga legacy | Selecao do pipeline | Preservar compatibilidade |
| telemetrySampleSeconds | 300, clamp 60..3600 | Legado; continua usa passo nativo fixo 10 s | Limites consolidados, cadencia inalterada |
| telemetryFlushSeconds | 900; ausencia antiga 3600; clamp 60..86400 | Legado; continua agrega minuto e usa janela propria | Limites consolidados, nao mudar defaults antigos |
| telemetryBufferMaxSamples | 2304 | Buffer legado | Preservar |
| telemetryBufferMaxAgeHours | 192 | Buffer legado | Preservar |
| telemetryWindowMaxBytes | 128 MiB, clamp 1..512 MiB | Fila continua persistida | Preservar limites/contadores de perda |
| telemetryWindowMaxAgeHours | 192, clamp 1..192 | Fila continua | Preservar |
| logPath | NightOwlPaths.AgentLogPath | Logger, Tray/diagnostico | Preservar configuracao; leitura limitada no Tray |
| statePath | NightOwlPaths.StatePath | Estado, checkpoints, Tray | Preservar aliases e dados existentes |
| pendingResultsPath | NightOwlPaths.PendingResultsDir | Resultados duraveis | Nao purgar para reduzir footprint |
| jobsPath | NightOwlPaths.StateDir | Estado de jobs | Preservar |
| installPath | NightOwlPaths.InstallDir | Lifecycle | Preservar ownership/validacoes |
| packagesPath | NightOwlPaths.PackagesDir | Normalizacao/preparacao e instaladores | Reservado, sem consumidor quente comprovado; preservar |
| cachePath | NightOwlPaths.CacheDir | Normalizacao/preparacao e instaladores | Nao apagar contrato publico |
| allowedJobTypes | Lista tipada do AgentConfig | Policy/dispatch/migracoes | Preservar remocoes explicitas; normalizar uma vez |
| softwareInstallEnabled | false | Instalacao tipada protegida | Preservar gate desligado |
| softwareCatalogSha256 | vazio | Validacao do catalogo aprovado | Preservar pin; nao habilitar por capacidade desconhecida |
| MachineIdSource / HasValidToken | Computados, JsonIgnore | Diagnostico/validacao | Nao sao novos campos persistidos |
| AgentState e aliases machine_id/machineId | Estado persistido | Cadencias, jobs, lifecycle | Preservar extension data, checkpoints e historico |
| NIGHTOWL_HOME | Ambiente | NightOwlPaths | Preservar isolamento e compatibilidade |
| NIGHTOWL_AGENT_CONFIG | Ambiente | ConfigService | Preservar selecao de config |
| NIGHTOWL_TEST_SERVER | Ambiente/teste | Guardas de teste loopback | Nao promover a configuracao de producao |
| KeepPowerShellAgent | Parametro instalador .NET; true | Preserva legado | Nao e AgentConfig; aposentadoria exige inventario |
| DisablePowerShellAgent | Switch instalador | Desabilita tarefa RMM-Agent-Heartbeat | Nao comprova parada do servico legado |
| KeepScheduledTaskFallback | Instalador de servico legado | Preserva tarefa de fallback | Coexistencia possivel; nao modificar automaticamente |
| WinswPath | Opcao legada | Avisa enquanto instalacao usa NSSM | Nao remover sem consumidores operacionais |

Outros limites existentes: inventario basico/rede diario, hardware detalhado semanal e volumes horario; Tray refresh 30 s; nova tolerancia local 120 s; updater health 180 s, quiesce 30 s e substituicao de arquivos 30 s; uninstaller espera Tray 10 s, agente 15 s e binarios 25 s. Nao foram alterados timeouts dos protocolos, lifecycle ou cadencias das DEC.

## 5. Legado e aposentadoria segura

`scripts/windows-agent/Install-RmmAgent.ps1` pode registrar `RMM-Agent-Heartbeat`, SYSTEM/ServiceAccount, IgnoreNew. `Install-RmmAgentService.ps1` usa NSSM para `NightOwlAgent`, LocalSystem, delayed-auto. Ambos podem coexistir com o .NET; sua presenca na frota nao foi medida.

O instalador .NET mantem fontes de identidade existentes, inclusive arquivos em `Agent` e `C:\RMM`, antes dos fallbacks ja definidos. Nenhuma migracao de identidade foi reescrita.

Plano futuro por endpoint: inventariar servicos/tarefas/comandos/caminhos e identidade forte; guardar configuracoes e definicoes de tarefas com ACL restrita; confirmar .NET autenticado, heartbeat novo, inventario e resultado de job com ACK; parar apenas o legado identificado, manter arquivos para reversao; observar duplicacao e continuidade; remover somente apos aprovacao. Se .NET falhar, restaurar a definicao anterior sem copiar credenciais entre endpoints. Nao apagar `C:\RMM` por idade ou nome.

## 6. Executaveis, tarefas e dependencias

| Componente | Modelo esperado | Dependencias relevantes |
|---|---|---|
| NightOwl.Agent.Windows | Servico NightOwlAgentDotNet, SYSTEM, residente | Shared; Http 8.0.1; Hosting.WindowsServices 8.0.1 |
| NightOwl.Agent.Tray | Sessao interativa, mutex por sessao; tarefa de logon existente | Shared; WinForms; ServiceController 8.0.1 |
| NightOwl.Agent.Updater | Sob demanda; runners de update/repair | Shared; WinForms; ServiceController 8.0.1 |
| NightOwl.Agent.Uninstaller | Sob demanda, fluxo tipado e receipt | Shared; ServiceController 8.0.1 |
| NightOwl.Agent.Diagnostics | Sob demanda, sem novo residente | Shared; EventLog e ServiceController 8.0.1 |
| NightOwl.Agent.Shared | Biblioteca, sem processo | BCL, sem pacote NuGet proprio |
| NightOwlAgent / RMM-Agent-Heartbeat | Legado condicional | NSSM/PowerShell; depende de inventario da frota |

Todos os projetos Windows permanecem net8.0-windows. Coletores/jobs PowerShell continuam temporarios e limitados pelos fluxos existentes. Tarefas GPO/bootstrap sao infraestrutura separada; nenhuma foi criada, removida ou alterada.

O builder oficial `scripts/Build-NightOwlAgentRelease.ps1` publica os cinco componentes self-contained no MESMO diretorio. Nao sao cinco runtimes independentes completos no ZIP final. Nenhuma dependencia foi comprovadamente removivel. Trimming, single-file e AOT nao foram aplicados: exigem homologacao de reflexao, serializacao, P/Invoke e lifecycle. O publicador antigo nao deve contornar o gate oficial.

Medicao indicativa de diretorios de BUILD framework-dependent durante a rodada: Windows 3.744.409 B/45 arquivos; Tray 1.058.951 B/12; Updater 1.186.716 B/12; Uninstaller 1.791.498 B/13; Diagnostics 1.844.001 B/13. Nao representam ZIP, pacote self-contained ou instalacao real. Nao houve publish nem medicao de tamanho instalado.

## 7. Disco, retencao e seguranca

| Recurso | Politica implementada hoje | Decisao/pendencia |
|---|---|---|
| Logs nativos/Tray/path/updater | Append; sem rotacao/quota global comprovada | Divida aberta; implementar somente com politica de evidencias aprovada |
| NSSM legado stdout/stderr | Rotacao propria de 10 MiB | Nao generalizar essa garantia aos logs .NET |
| Telemetria continua | 128 MiB/192 h por default; perdas explicitas | Preservar; nao prometer retencao ilimitada |
| Jobs V2 | Admissao 24, resultados 100; journal 32/2 MiB, registro 128 KiB; quarentena 128 arquivos/16 MiB | Backpressure e limites preservados |
| PendingResultQueue compartilhada | Defaults 1000/128 MiB, payload 256 KiB, retencao 14 dias; enviados 1000/14 dias | Criticos protegidos; politica legada nao equivale a nunca perder pendente |
| Pendentes legados nao criticos | Prune pode remover por idade/quota antes de ACK | Divida preexistente a revisar; V2 usa Critical. Nao alterar destrutivamente nesta rodada |
| Backups updater/rollback | Sem politica geral segura por idade | Manter ate terminal confirmado, receipts/ACK reconciliados e aprovacao |
| Runners updater | Limpeza apenas terminal, ownership/nome validados, fora do atual e sem uso | Preservar validacoes existentes |
| Diagnostics | Sob demanda, log individual 2 MiB, total 10 MiB, arquivo 25 MiB; staging limpo no finally | Arquivo entregue nao tem expurgo etario automatico |
| Cache/pacotes/temporarios MSI | Vinculados aos fluxos existentes | Sem expurgo generico novo; resultado incerto exige revisao |
| Config/identidade/trust/checkpoints | Dados essenciais | Nunca limpar por idade; preservar ACL e recovery |

Proposta, NAO implementada: logs ativos de ate 10 MiB com cinco rotacoes; evidencias de erro/diagnosticos exportadas a armazenamento restrito e retidas por 30 dias, sujeito a politica corporativa. Pendentes, identidade, trust e backups necessarios nao entram em limpeza por idade. Antes de implementar: definir leitor concorrente, exportacao, falha de disco cheio, ownership e testes de crash/rotacao. Esta pendencia impede declarar higiene integral concluida.

## 8. Testes e comparacao

Resultados locais reais; fixtures nao equivalem a homologacao de servico instalado.

| Validacao | Resultado |
|---|---|
| Build dos dez projetos .NET apos alteracoes | PASS, zero warnings/errors |
| Windows suite completa | FAIL preexistente em TestRepairRunnerScriptPersistsCompletedResult; grupos anteriores passam, igual ao baseline |
| Windows configuracao focada | PASS: migracoes, allowlist, limites e roundtrip |
| Heartbeat/inventario modular/telemetria focados | PASS; Lote 1 reexecutado ao final, uma normalizacao fisica e zero subprocessos PowerShell |
| Shared + politicas/leitura real do Tray | PASS: UTF-8, BOM, CRLF, malformado, append, rotacao, fallback, startup/reconexao e grace limitado |
| Updater | PASS |
| Uninstaller | PASS |
| Diagnostics fixture isolada | PASS: 15 arquivos, ZIP 5810 B, manifesto e sentinela sanitizada, rede desabilitada |
| Django focado nas DEC e contratos E2E | PASS: 87 testes, 2 skips explicitos (PostgreSQL/browser opt-in) |
| Worker -> backend -> jobs imediatos/recorrentes -> receipt .NET | PASS na fixture E2E local |
| PostgreSQL | NAO EXECUTADO; PostgreSQL/Docker indisponiveis neste ambiente |
| Tray grafico/SCM/restart/task logon reais em VM | NAO HOMOLOGADOS; politicas e leitores foram executados em fixtures |
| git diff --check | PASS |

Benchmark sintetico de arquivo: 10.400.000 bytes; leitura nova 1.048.577 bytes incluindo fronteira. Ultima execucao pareada: integral 39,606 ms, limitada 5,710 ms (outra rodada: 68,492/6,419 ms). Reducao de bytes lidos aproximadamente 89,9%; nao e SLA nem medicao de CPU/RSS do servico ou do Tray real. A leitura limitada nao segue append indefinidamente.

Estimativa de eventos evitados com refresh de 30 s: ate 2880 logs Running repetidos/dia e 2880 repeticoes de bootstrap/path por dia, quando o Tray fica aberto continuamente. Nao e volume medido na frota.

CPU em repouso, RSS, startup, trafego, processos ativos e tamanho instalado antes/depois: nao medidos em endpoint isolado com servico instalado. Nao substituir por numeros de fixtures. A remocao de PowerShell do heartbeat e a normalizacao unica de discos do Lote 1 permanecem preservadas.

Reproducao local apos build: executar os projetos Tests; no Windows.Tests, usar tambem `--configuration-consolidation-only`, `--hardware-only`, `--telemetry-only` e `--cleanup-only`. Executar `tests/Test-NightOwlDiagnosticsFixture.ps1` apos build de Diagnostics. A fixture usa somente diretorio temporario proprio, restaura NIGHTOWL_HOME e desabilita testes de rede.

## 9. Riscos, homologacao e proximos gates

1. Desenvolvimento: falta politica implementada de rotacao dos logs nativos e revisao da retencao de pendentes legados nao criticos. Nao apagar evidencias para aparentar footprint menor.
2. Operacao: falta inventario de servico/tarefa PowerShell na frota para provar ausencia de duplicacao e executar aposentadoria controlada.
3. Homologacao: validar Tray em logon com servico pending, parada/restart menor e maior que dois minutos, consulta SCM negada, multiplas sessoes, atualizacao e uninstaller. Grace nao reinicia o Tray apos encerramento; proximo logon/inicio explicito continua sendo o mecanismo existente.
4. Lifecycle: validar update/repair/rollback assinado em VM, preservar ownership e receipts. Falha preexistente do repair runner bloqueia gate de release; nao foi corrigida nem mascarada aqui.
5. Backend: executar migrations/testes PostgreSQL isolados e browser opt-in antes de homologacao integral das automacoes.
6. Pacote: medir runtime self-contained e instalacao real, sem sacrificar recuperacao ou trust para reduzir tamanho.

Riscos para DEC-04C/04D: ampliar terminal/scripts/instalacao sem resolver evidencias, limites e gate de repair aumentaria superficie operacional; capacidades desconhecidas nunca devem autorizar operacoes; nao misturar sinal rapido com execucao privilegiada arbitraria. Pontos de extensao existentes foram mantidos, nenhuma dessas DEC foi iniciada.

## 10. Inventario desta rodada e conclusao

Alterados nesta rodada: `NightOwl.Agent.Windows/Services/ConfigService.cs`, `TelemetrySettings.cs`; `NightOwl.Agent.Windows.Tests/Program.cs`; `NightOwl.Agent.Tray/AgentLocalState.cs`, `Program.cs`, `TrayApplicationContext.cs`; `NightOwl.Agent.Shared.Tests/Program.cs` e seu csproj.

Adicionados: `NightOwl.Agent.Windows.Tests/ConfigurationConsolidationTests.cs`; `NightOwl.Agent.Shared/LogTailReader.cs`; `NightOwl.Agent.Tray/ServiceObservation.cs`; `NightOwl.Agent.Shared.Tests/LocalFootprintTests.cs`; `tests/Test-NightOwlDiagnosticsFixture.ps1`; este relatorio. Demais arquivos sujos pertencem as implementacoes anteriores e nao constituem alteracoes novas desta rodada.

Conclusao: melhorias seguras implementadas e testadas; nenhuma regressao nova identificada nos testes executados. **Simplificacao parcial**, nao encerramento integral da divida. **NO-GO para publicacao/atualizacao de endpoints** ate resolver o gate preexistente e homologar os pontos acima. Aguardar aprovacao; nao iniciar DEC-04C automaticamente.
