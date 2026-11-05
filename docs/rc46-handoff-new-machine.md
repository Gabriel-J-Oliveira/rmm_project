# RC46 - Handoff para nova maquina

Data de corte: 2026-10-30. Este documento e a entrada principal para continuar o desenvolvimento sem o historico do chat.

## 1. Estado transferido e limites

- Repositorio: https://github.com/Gabriel-J-Oliveira/rmm_project
- Branch exclusiva de desenvolvimento: `feature/rc46-agent-core`.
- Base confirmada por fetch: `0d5d8ceb28928b6a580cdff15a5144b373093296`, tambem origin/main nesta rodada. Nao presume SHA de producao: producao nao foi consultada.
- Snapshot integrado: `901898c1e27f2137d4a6f51bb13dd226601bf8db`, 107 arquivos, 9587 insercoes/467 remocoes. Tree: `04d1e2147f57db8e0bc676c2845e932896d36172`.
- O commit seguinte adiciona este handoff e `rc46-next-task-agent-closure.md`. O SHA da ponta deve ser obtido de `git rev-parse HEAD` e confirmado contra o remote; nao e possivel escrever o SHA do proprio commit no seu conteudo.
- Snapshot integrado, nao cinco commits historicos artificiais: Worker, ConfigService, API e testes foram modificados por varias DEC antes de existir commit. Inventario exato: `git show --name-status 901898c`; mudancas completas: `git diff 0d5d8ceb..901898c`.
- **Desenvolvimento preservado, nao RC46 homologada. Simplificacao parcial; publicacao NO-GO.**

Autorizacao deste handoff: commit e push somente desta branch. Nao autoriza merge/main, deploy, release, canal estavel, instalacao, migracao em producao, jobs reais ou atualizacao de endpoints. Nenhuma limpeza adicional foi implementada durante a transferencia. Outras branches/worktrees nao foram modificadas.

Os relatorios das DEC descrevem os resultados e restricoes na data de cada incremento; frases como "uncommitted" ou "no push" nesses relatorios sao historicas. Este documento registra a consolidacao posterior, nao invalida os testes ou bloqueios anteriores.

## 2. Arquitetura

NightOwl e um RMM com Django/DRF, PostgreSQL em producao, painel de operadores e agente Windows .NET 8. O backend armazena identidade, heartbeat, inventarios, telemetria, jobs, resultados e auditoria. SQLite atende testes locais, nao comprova concorrencia PostgreSQL.

`NightOwlAgentDotNet` e o servico Windows SYSTEM. `Worker.cs` coordena rotinas independentes de heartbeat, inventario, telemetria e jobs; nao ha um novo servico por funcionalidade. Tray e um processo por sessao interativa, iniciado pelo mecanismo de logon existente, nao o executor privilegiado.

Comunicacao agente -> backend e outbound pela URL configurada (HTTPS/443 na implantacao normal), com bearer individual e validacao TLS. Enrollment e hardening de identidade da base sao preservados; nao reassociar por serial, copiar credenciais ou relaxar quarentenas. WinRM/GPO dizem respeito a preparacao/instalacao inicial e nao sao requisitos do ciclo normal de heartbeat/jobs/update do agente instalado. HTTP de loopback nos testes e estritamente fixture local, nao orientacao para producao.

Operador usa autenticacao de sessao, CSRF e permissoes tecnicas. Os agentes nao aceitam shell arbitrario: jobs tipados passam por allowlist, payload, identidade/capacidade, validade, ownership e politica de execucao. Capacidade anunciada nao e autorizacao suficiente.

Updater, Uninstaller e Diagnostics executam sob demanda. Shared concentra caminhos, arquivos, filas, estado, trust e lifecycle. Update preserva assinatura, hashes, ownership, handshake e rollback; desinstalacao tem receipts e protecoes; Diagnostics tem limites e sanitizacao. Nao retirar esses mecanismos para diminuir o pacote.

## 3. Evolucao RC46 verificada no codigo

| Incremento | Implementacao e arquivos principais | Estado |
|---|---|---|
| DEC-01 | Worker.RunHeartbeatLoopAsync, WindowsInventoryCollector.BuildHeartbeat; AgentDtos; agents/serializers.py e services.py; LightweightHeartbeatTests; agents/test_lightweight_heartbeat.py | Heartbeat nativo leve sem CIM/WMI/PowerShell, independente de inventario/jobs. Ausencia de campos nao apaga evidencia previa; proveniencia explicita. Testes locais passam |
| DEC-02 | Collectors/InventoryCadence.cs, WindowsInventoryCollector.cs, HardwareInventoryNormalizer.cs; AgentState; agents/services.py; endpoint_detail.js; HardwareCadenceTests e teste PS | Basico/rede diario, detalhes semanal, volumes horario, auxiliares na cadencia compativel. Unknown/failed nao inventa ausencia; detalhes preservam datas. OEM real pendente |
| DEC-03 | ContinuousNativeReader, NativeDiskCounters, ContinuousTelemetryPipeline, TelemetryMinuteAggregator/WindowQueue; agents/telemetry_continuous.py, telemetry_live.py, telemetry_history.py; endpoint_live.js/capacity.js | Amostragem nativa ~10 s, agregacao minuto UTC, persistencia e ACK, backlog limitado, live separado de historico. Legado v1 mantido; carga sustentada e infraestrutura pendentes |
| DEC-04A | Jobs/JobRuntime.cs, JobJournal.cs, OwnedProcess.cs; JobExecutionCoordinator/Policy/Executor; AgentApiClient; Shared/JobState/FileStore; agents/job_control.py, internal_jobs.py; jobs.js | Sinal pequeno, pull/start/execute/result/ACK separados, inbox e resultados duraveis, deduplicacao e reconciliacao. Nao promete exactly-once fisico. Sem WebSocket implementado |
| DEC-04B | agents/automation.py; models.py; dashboard/automation_views.py; process_job_automations; job_automations.js/template e testes | Calendario no backend, permissoes, snapshot de alvo, UTC/IANA, ocorrencias e retries limitados. Scheduler desligado por default. PostgreSQL e ativacao operacional pendentes |
| Lote 1 | Mapeamento basico/merge em WindowsInventoryCollector/HardwareInventoryNormalizer; CleanupPhase1Tests | Redundancias comprovadas removidas sem perda de campos |
| Capacidade V2 | Worker.SendHeartbeatAsync; agents/automation.py.known_v2; testes de protocolo reais locais | Canonico jobs_protocol_v2 mais alias job_protocol_v2; sem capacidade V2, agente legado nao recebe automacao V2 |
| Limpeza parcial | ConfigService/TelemetrySettings; Tray; Shared/LogTailReader; ConfigurationConsolidationTests/LocalFootprintTests | Menos normalizacao, leitura e logs repetidos. Sem rotacao nativa geral implementada |

Migrations necessarias e incluidas: `0037_continuous_telemetry` (janelas/live), `0038_typed_internal_jobs` (tipos internos), `0039_job_automations` (definicoes/ocorrencias/health). A ordem parte de0036. Nao executar em banco corporativo durante onboarding; validar em PostgreSQL descartavel antes de qualquer plano de deploy.

Servicos e instalacao protegida de software permanecem internos/gated. `NIGHTOWL_SERVICE_COLLECTION_ENABLED` e `NIGHTOWL_SOFTWARE_INSTALL_ENABLED` sao consultados com default falso; `softwareInstallEnabled` e pin de catalogo tambem restringem o agente. Nao habilitar criando capacidades desconhecidas. DEC-04B nao permite lifecycle recorrente, shell ou instalacao de software.

Scheduler: comando `process_job_automations`, flag `NIGHTOWL_JOB_AUTOMATIONS_ENABLED=false`. Nao criar timer de sistema automaticamente. Desligar somente a flag nao cancela jobs ja entregues; outcome incerto requer reconciliacao.

## 4. Limpeza, footprint e pendencias

Detalhes e matriz completa: `rc46-agent-cleanup-final.md`; Lote 1: `rc46-agent-cleanup-phase1.md`.

- Allowlist normalizada uma vez; limites de intervalos legados compartilhados. Nenhum campo publico removido.
- Tray nao faz bootstrap de caminhos em cada refresh. Leitura limitada a 1 MiB + byte de fronteira/400 linhas, sem seguir crescimento infinito. Heartbeat persistido permanece fallback.
- Tray tolera servico nao-running por dois minutos monotonicos, nao reinicia servico; missing/inacessivel encerra. A tolerancia nao e homologacao real de SCM/GUI.
- Logs Running somente por transicao; ServiceController descartado em falha.
- Benchmark sintetico anterior: 10.400.000 -> 1.048.577 bytes lidos (-89,9%), 39,606 -> 5,710 ms em uma rodada. Reexecucao no handoff: 66,431 -> 8,470 ms. Variacao local, nao SLA, RAM/CPU da frota ou tamanho instalado.
- Lote 1: normalizacao fisica uma vez, heartbeat sem subprocesso PowerShell. Estruturas sinteticas basicas 496 B/chamada, merge 11312 B/chamada.
- Nenhum executavel/runtime removido; nao afirmar reducao de tamanho do ZIP/instalacao. Builder publica componentes self-contained no mesmo diretorio.
- Logs nativos nao tem rotacao/quota global completa. Proposta 10 MiB x5 e retencao de evidencias depende de aprovacao; NAO implementada.
- Filas V2 usam resultados criticos e backpressure. Prune legado de nao criticos ainda pode remover pendente por idade/quota; nao generalizar garantia V2 para todo historico.
- Backups de rollback e outcomes incertos nao devem ser expurgados por idade.
- Legado PowerShell: servico NSSM NightOwlAgent e tarefa RMM-Agent-Heartbeat podem coexistir. KeepPowerShellAgent=true; DisablePowerShellAgent so desabilita a tarefa, nao comprova parada do servico. Inventario da frota e confirmacao de migracao sao pre-requisitos de aposentadoria. Nenhuma desativacao executada.
- Repair runner: falha preexistente reproduzida na base limpa e novamente neste handoff. `TestRepairRunnerScriptPersistsCompletedResult`, mensagem `Repair runner pending result should be completed.` Nao remover/pular/enfraquecer.
- Pacote RC46 Pilot: NAO gerado, assinado, publicado ou instalado. MSI assinado homologado, publicacao Pilot pausada/rollout zero e gates do builder ainda pendentes. Nao sobrescrever RC45 nem promover stable.

## 5. Preparar a nova maquina

Preferir Windows x64 para suites nativas, WinForms, PowerShell e lifecycle. Linux pode testar Django/Node, mas nao substitui os gates Windows.

Ferramentas: Git; Python 3.13 ou 3.14 compativel com requirements; .NET SDK8; Windows PowerShell5.1 e PowerShell7 para scripts; Node.js para frontend; navegador Chromium/Chrome e Playwright opcionais. Neste handoff: Python3.14.6, SDK8.0.422, Node24.22.1, Django6.0.5, DRF3.17.1, psycopg3.2.13. `requirements.txt` usa faixas, portanto um clone reproduz codigo mas nao fixa todas as versoes futuras de dependencias. Conferir versoes antes de comparar resultados.

PowerShell, em pasta de desenvolvimento nova:

```powershell
git clone --branch feature/rc46-agent-core --single-branch https://github.com/Gabriel-J-Oliveira/rmm_project.git
Set-Location rmm_project
git status --short --branch
git rev-parse HEAD
git ls-remote origin refs/heads/feature/rc46-agent-core
py -3.14 -m venv .venv
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m pip install -r requirements.txt
```

Se Python3.13 for a versao provisionada, usar `py -3.13`. Nao copiar o venv da maquina antiga, `.env`, caches, logs ou SQLite. Nao executar `NightOwl.Agent.Windows.exe` como aplicacao de teste: isso inicia o Worker real; executar os projetos Tests.

Ambiente somente de teste, sem importar credenciais corporativas:

```powershell
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
$env:AD_COMPUTER_DISCOVERY_ENABLED='False'
$env:NIGHTOWL_JOB_AUTOMATIONS_ENABLED='False'
$env:NIGHTOWL_TELEMETRY_LIVE_CACHE=Join-Path $env:TEMP 'nightowl-rc46-local-live'
```

A chave acima e sintetica, nunca usar em producao. Nao criar `.env` a partir de valores corporativos. `.env.example` documenta nomes sem segredos: DATABASE_URL, DJANGO_SECRET_KEY, flags de debug/hosts/CSRF, AD opcionais, URLs, WinRM trust e gates. Os testes usam settings isolados; `check` sem settings de teste pode herdar DATABASE_URL, por isso explicitar sempre.

AD, SMTP, WinRM e servidor corporativo nao sao necessarios para os testes locais abaixo. NuGet/pip precisam de repositorios de dependencias ou cache aprovado. PostgreSQL e obrigatorio para homologar transacoes/races, nao para reproduzir SQLite. Assinatura/publisher requerem chaves e infraestrutura de release fornecidas por canal protegido, nunca Git/chat; nao sao necessarias para build de desenvolvimento.

## 6. Build e testes reproduziveis

Na raiz do clone, PowerShell:

```powershell
$projects=Get-ChildItem -Directory -Filter 'NightOwl.Agent.*' | ForEach-Object {
    Get-ChildItem -LiteralPath $_.FullName -Filter '*.csproj'
}
foreach($project in $projects) {
    dotnet build $project.FullName -c Release --nologo
    if($LASTEXITCODE -ne 0){throw 'Build failed'}
}

dotnet run --project NightOwl.Agent.Shared.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Updater.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Uninstaller.Tests -c Release --no-build
$exe='./NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.exe'
foreach($mode in @('--configuration-consolidation-only','--cleanup-only','--hardware-only',
                  '--jobs-only','--continuous-only','--telemetry-only','--handshake-only')) {
    & $exe $mode
    if($LASTEXITCODE -ne 0){throw 'Focused Windows gate failed'}
}
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-NightOwlDiagnosticsFixture.ps1
& $exe # Suite completa: falha conhecida do repair runner, manter visivel.
```

**Updater.Tests deve usar `dotnet run --no-build` ou o .exe**, nao `dotnet <dll>`: o teste cross-process relanca Environment.ProcessPath. Via dll, esse caminho e dotnet.exe, sem o nome da DLL, e ocorre `Child runner must acquire named mutex`. Foi demonstrado FAIL pela invocacao inadequada e PASS pelo apphost, sem mudar testes/implementacao. Build deve terminar antes de iniciar as suites E2E .NET/Django para nao substituir DLLs em uso.

```powershell
& $python manage.py check --settings=config.settings_test
& $python manage.py makemigrations --check --dry-run --settings=config.settings_test
& $python manage.py test agents.test_lightweight_heartbeat agents.test_hardware_cadence agents.test_continuous_telemetry agents.test_telemetry_protocol_e2e agents.test_job_control agents.test_jobs_protocol_e2e agents.test_automation agents.test_automation_protocol_e2e --settings=config.settings_test_browser --noinput

node tests/frontend/test_endpoint_hardware.cjs
node tests/frontend/test_endpoint_live.cjs
node tests/frontend/test_endpoint_detail_jobs_static.js
node tests/frontend/test_endpoint_detail_update_modal.js
node --check static/js/job_automations.js
git diff --check
```

Settings browser usa SQLite temporario por processo para HTTP concorrente, nao banco de producao. Os E2E exercitam Worker/heartbeat real, Django HTTP, jobs e ACK .NET em loopback com dados sinteticos. Alguns usam collect_logs local limitado; nao executam em maquinas corporativas. Browser visual e opt-in `NIGHTOWL_TEST_BROWSER_E2E=1`, exige Node/Playwright/Chrome e possivelmente NODE_PATH configurado localmente; nao copiar caminho de cache da maquina antiga. Sem opt-in, o teste e skipped, nao PASS visual.

Gate PostgreSQL: provisionar instancia descartavel em loopback com base `nightowl_rc46_isolated`. Definir NIGHTOWL_TEST_POSTGRES_URL por canal local protegido (nao imprimir URI com senha), sem query/fragment e sem apontar para producao. Executar:

```powershell
& $python manage.py test agents.test_automation --settings=config.settings_test_automations_postgres --noinput
```

Django cria banco de teste separado por processo. Expandir gates de migracao, delivery/start/result, cancelamento concorrente e carga conforme os relatorios; um teste de dois schedulers nao prova toda a concorrencia.

## 7. Evidencias desta transferencia

| Gate reexecutado | Resultado |
|---|---|
| Build dos dez projetos .NET Release | PASS, zero warnings/errors |
| Shared + leitura real/politica pura do Tray | PASS |
| Windows focados: config/cleanup/hardware/jobs/continuous/telemetry/handshake | PASS |
| Updater via apphost | PASS; via dotnet dll FAIL de invocacao documentado |
| Uninstaller | PASS |
| Diagnostics sem rede, fixture sintetica | PASS, 15 arquivos, ZIP5810 B, sentinela e hashes |
| Django contratos das DEC e E2E locais | PASS, 89 testes em50,966 s, dois skips (PostgreSQL e browser opt-in) |
| Django check / makemigrations dry-run | PASS / PASS, nenhuma migration adicional necessaria |
| Node renderer hardware/live, jobs/update static e sintaxe JS/CJS alterados | PASS |
| Scripts de hardware com CIM sintetico em PowerShell5.1 e7 | PASS nos dois runtimes |
| Windows suite completa | FAIL conhecido: repair runner, mesma assercao; nenhum skip novo |
| PostgreSQL / carga sustentada / lifecycle instalado / Tray GUI real | NAO HOMOLOGADOS |

Logs de erros negativos esperados, CSRF negado, live-cache indisponivel simulado e broken pipes loopback apareceram em testes aprovados; nao sao logs de producao. Screenshots anteriores e bancos/logs de teste nao foram transportados. Os resultados mais amplos de rodadas anteriores permanecem nos documentos das DEC, nao foram todos repetidos para o handoff.

## 8. Seguranca do push e itens nao transportados

Antes de commit: git status, branch/HEAD, worktrees, remote, marcadores de merge/rebase/cherry-pick e index.lock verificados; nenhum em andamento. Index estava vazio. Somente esta worktree foi staged. Revisados inventario, tamanhos, diff, fixtures e padroes de segredo; literais encontrados eram sinteticos de testes/documentacao, nao credenciais reais. Nenhum arquivo novo/alterado excedeu1 MiB. Nao se trata de certificacao automatica de ausencia de segredos em todo historico antigo do repositorio.

CI versionado: `enrollment-identity.yml` roda em PR/dispatch, sem deploy; `nightowl-agent-release.yml` e apenas workflow_dispatch, exige ambiente de release, e pode publicar SOMENTE se acionado explicitamente. Push desta branch nao aciona esses workflows. Consulta autenticada aos webhooks do repositorio retornou lista vazia; nenhum hook Git local ativo encontrado. Nao disparar release manual ou abrir PR de integracao neste handoff.

Antes do push, API de Releases retornou lista vazia e Actions retornou zero runs nesta branch. Isso nao inventaria o catalogo de releases do backend: nenhum servidor corporativo foi acessado. O envio usa somente git push desta branch, sem tags ou force.

Excluidos: bin/obj dos dez projetos, __pycache__, logs/, test-nightowl.sqlite3 e demais ignorados. Nenhum desses itens e necessario para recuperar codigo; rebuild/testes os recriam. Ignorados de runtime podem conter dados sensiveis e nao devem ser copiados indiscriminadamente.

Nao transferiveis apenas com Git: SDKs/venvs, ambiente PostgreSQL descartavel, VM Windows e snapshots, certificados/chaves de assinatura com ACLs, configuracoes protegidas, acesso autorizado ao laboratorio, evidencias/screenshots temporarios e inventario real da frota legada. RC46 nao exige trazer dados privados de Office/OBZ, dumps ou tokens de endpoints para continuar.

## 9. Roadmap e decisao

Ordem proposta, sem execucao automatica:

1. Encerrar limpeza do agente: retencao, repair runner, footprint/processos, legado e lifecycle.
2. Preparar e homologar RC46 Pilot, sem rollout automatico.
3. DEC-04C: biblioteca de scripts e atalhos, mediante nova aprovacao.
4. DEC-04D: terminal remoto ConPTY, mediante nova aprovacao.
5. DEC-05: homologacao completa do Tray (roteiro preliminar pode ser preparado antes).
6. Gate final RC46 com PostgreSQL, carga100 agentes, TLS/reconexao, lifecycle, assinatura e canario aprovado.

Bloqueios explicitos: repair runner; PostgreSQL nao homologado; carga sustentada WSGI/proxy e100 agentes nao homologada; lifecycle real pendente; Tray real pendente; MSI assinado/contrato interno de software e pacote/publicacao Pilot pendentes; logs nativos e aposentadoria do legado ainda parciais.

**Proxima tarefa autorizada de desenvolvimento:** ler `rc46-next-task-agent-closure.md`. Nao iniciar DEC-04C, integrar main, publicar ou operar producao. O handoff preserva o estado de desenvolvimento; nao transforma testes sinteticos em homologacao.
