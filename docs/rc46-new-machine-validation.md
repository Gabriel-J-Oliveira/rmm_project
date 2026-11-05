# RC46 — Validação na nova máquina

Nota histórica: este documento registra a primeira entrega local. A revisão
seguinte, com commits locais autorizados, está no fechamento final, no relatório
de footprint e no roteiro Pilot; seu estado prevalece sobre o snapshot abaixo.

Data local: 2026-10-30, America/Sao_Paulo. Entrega para revisão; sem commit/push.

## Checkout, localização e preservação

- Remote: `https://github.com/Gabriel-J-Oliveira/rmm_project.git`.
- Branch recebida: `feature/rc46-agent-core`.
- SHA recebido e confirmado novamente por fetch/ls-remote:
  `6fe53cac4f064013fa6993b8c4c56041450d26b9`.
- Snapshot integrado ancestral: `901898c1e27f2137d4a6f51bb13dd226601bf8db`.
- Branch de trabalho criada: `feature/rc46-agent-cleanup-closure`.
- Checkout inicialmente limpo, completo, sem substituir por main. Migrations
  0037_continuous_telemetry, 0038_typed_internal_jobs e 0039_job_automations presentes.
- Local final: `C:\Users\gabri\Downloads\Nightowl RMM\rmm_project`.
- A pasta foi movida para fora de NetStudy por solicitação explícita. O status de
  NetStudy manteve as nove modificações e `docs/` não rastreado encontrados no
  início; nenhum arquivo desse projeto foi modificado por esta tarefa.
- A pasta antiga `C:\Users\gabri\Downloads\NetStudy\Nightowl RMM` ficou vazia.
  A primeira tentativa de mover sua raiz encontrou lock de outro processo;
  mover seu conteúdo para a pasta irmã funcionou. Remover a raiz vazia depois
  foi rejeitado pela revisão automática (`blocked by policy`).

HEAD continua no SHA recebido: as alterações novas são somente locais e não
staged. Não houve merge, reset ou descarte de trabalho anterior.

## Ferramentas e preparação

| Item | Estado observado |
| --- | --- |
| Sistema | Windows x64, build 10.0.26200 |
| Git | Disponível; clone, fetch e consulta da branch funcionaram |
| Python | 3.14.4; `.venv` criado do zero |
| Django / DRF | 6.0.9 / 3.17.2 |
| psycopg | 3.2.13 com binary |
| cryptography | 50.0.2 |
| SDK .NET inicial | Ausente; havia apenas runtimes globais |
| SDK preparado | 8.0.422 em `%LOCALAPPDATA%\NightOwlDev\dotnet`, instalado sem alterar PATH global |
| Runtime do SDK local | 8.0.28, incluindo WindowsDesktop |
| PowerShell | Windows PowerShell 5.1.26100.9711 e PowerShell 7.6.5 |
| Node | v24.19.0 |
| Navegador | Chrome disponível; Playwright do runtime local disponível |
| PostgreSQL / Docker | Nenhum postgres/pg_ctl/psql/docker encontrado no PATH |
| Trust/signing | Roots autorizadas e chave de release não provisionadas; não foram geradas substitutas |

`requirements.txt` foi instalado sem modificar suas faixas. O freeze efetivo está
em `artifacts/rc46-validation/python-packages.txt`; as versões diferem do host do
handoff e os resultados abaixo são reexecuções nesta máquina, não resultados
copiados. O download de ferramentas/dependências usou fontes públicas; nenhum
servidor corporativo foi acessado. O instalador do SDK fica em `.local-tools/`,
excluído apenas no `.git/info/exclude` deste checkout.

## Arquitetura e componentes revisados

Lidos README, DEV_SETUP, handoff, plano de fechamento, relatórios das DEC-01,
02, 03, 04A, 04B, capabilities V2, limpeza fase 1/final e documentação de build,
trust, distribuição, compatibilidade, identidade e lifecycle.

Inspecionados Worker/Program, ConfigService, JsonlLogger, coletores/cadências,
pipeline contínuo, motor/journal/policy de jobs, Shared/path/file store/filas/trust,
Tray/state/service observation/timer, Updater/recovery/backup/limpeza de runners,
Uninstaller/receipts/autorizações, Diagnostics/limites/staging, instaladores e
scripts PowerShell legados. Conferidos consumidores Django de heartbeat,
automação, delivery/start/result e migrations, sem refatorá-los.

As funcionalidades RC46 recebidas foram preservadas. O relatório
`rc46-agent-final-closure.md` registra alterações, remoções, decisões de retenção,
diagnóstico do repair e preservações de compatibilidade.

## Evidências executadas

Logs completos locais: `artifacts/rc46-validation/`. Arquivos gerados/fixtures
e bancos temporários não são parte do diff de código.

| Gate | Resultado nesta máquina |
| --- | --- |
| Build Release dos dez projetos, baseline | PASS, dez exits 0 |
| Windows completo original | PASS; a falha histórica não ocorreu sem alterar a fixture |
| Nova reprodução com caminhos com espaços, antes do fix | FAIL na mesma asserção de completed; log `repair-spaces-baseline.log` |
| Repair focado após correção | PASS: sucesso, exit 7, fila indisponível, caminhos com espaços/apóstrofo e roundtrip nativo de argumentos |
| Shared completo, incluindo Tray | PASS: retenção, redaction, limite total, leitores/rotação concorrentes, 420 registros entre threads/processos, rename negado, read-only e disco cheio injetado |
| Windows completo final | PASS, tanto execução direta como pelo builder oficial |
| Configuração consolidada / cleanup | PASS, allowlist, migrações, shape/cadências e equivalência de inventário |
| Hardware e heartbeat | PASS, incluindo isolamento e cancelamento |
| Jobs / telemetria contínua / telemetria legada / handshake | PASS nos modos focados e suíte completa |
| Updater | PASS via apphost e pelo `dotnet run` do builder; não usado `dotnet Updater.Tests.dll` |
| Uninstaller | PASS em fixtures; nenhum endpoint foi desinstalado |
| Diagnostics | PASS, 15 arquivos, ZIP de 5.649 B, hashes/sentinela sanitizada; rede desabilitada |
| Hardware PowerShell/CIM simulado | PASS em PowerShell 5.1 e 7 |
| Lifecycle / ACL / trust bootstrap de scripts | PASS em PowerShell 5.1, inclusive no builder |
| Novos helpers de log lifecycle | PASS em 5.1 e 7; nenhum entrypoint de instalação executado |
| Inventário somente leitura do legado | PASS em fixtures 5.1/7, inclusive identidade conflitante e consulta negada |
| Django check | PASS |
| makemigrations --check --dry-run | PASS, nenhuma migration adicional |
| Regressão Django ampla relevante | PASS: 717 testes, 53,985 s, 31 skips explícitos |
| DEC/contratos/Chrome com SQLite temporário em arquivo | PASS: 89 testes, 37,746 s, 1 skip PostgreSQL |
| .NET -> Django -> Chrome | PASS para live/CSRF, jobs e agenda real; recorrência seguinte usa relógio simulado |
| Node hardware/live/jobs/update e sintaxe automations | PASS |
| Fleet UI administrativo sintético opt-in | PASS: 1 teste, 2,964 s, Chrome com requests interceptados |
| Builder oficial local, sem SkipTests e sem Publish | BLOCKED após testes e publish local dos cinco componentes: RELEASE_TRUST_ROOTS_MISSING |
| ZIP de inspeção de desenvolvimento | PASS para presença, tamanhos e SHA-256 de todos os 512 arquivos; unsigned, não publicável |
| git diff --check | PASS, exit 0 com configuração normal do checkout |

As advertências LF/CRLF de Git não são falhas de whitespace. Uma tentativa de
diagnóstico com `core.autocrlf=false` tratou CRLF de arquivos existentes como
mudanças/whitespace; não foi usada para avaliar o diff nem motivou reformatar
arquivos. A verificação final acima usa o comando normal requerido.

Falhas intermediárias do gerador de artefato foram corrigidas: acesso a tamanho
de OrderedDictionary no PowerShell 5.1, nomes de entradas ZIP normalizados para
`/` e carga explícita de System.IO.Compression. A execução final verificou cada
arquivo extraído logicamente pelo stream ZIP, sem confiar somente na listagem.

## Medições locais e seus limites

Harness nativo de sete leituras: média 38,12 ms, máximo 85,26 ms, CPU média do
harness 0,035%, maior média por leitura 5,711%, pico working set 50.753.536 B.
Seis deltas CPU válidos, sete leituras de memória, 48 valores de disco,
19.975 B de payload e zero PowerShell. `complete_minute_windows=0` nessa execução;
não foi declarada uma janela completa onde o harness não a confirmou. Os testes
determinísticos e E2E de agregação/janelas passaram separadamente.

Leitura de log sintético: 10.400.000 B de origem, até 1.048.577 B lidos pelo leitor
limitado já existente; última rodada Shared focada 53,239 ms integral / 5,761 ms
limitada. Isso não é melhoria nova desta rodada nem benchmark do serviço instalado.

Nenhuma medição de CPU/RSS/IO/startup ou tamanho instalado antes/depois em endpoint
foi fabricada. Não há novo residente, timer ou pacote NuGet. O ZIP self-contained
de desenvolvimento tem 72.012.389 B e payload de 171.512.442 B, compartilhando
runtime entre cinco componentes; esses números não são tamanho de instalação.

## Pacote preparado e Pilot

Artefato local verificado:

```text
artifacts/rc46-development/20261030T145152Z-a3a25850f5954584bf82c4d248a7fd89/
  NightOwl-RC46-development-UNSIGNED-NOT-FOR-INSTALL.zip
  development-manifest.json
  artifact-verification.json
  payload/NOT-FOR-INSTALL.txt
  payload/<cinco executáveis, Shared e dependências>
  build-output/<saída local de publish para inspeção>
```

SHA-256 do ZIP:
`3e38cd7b8b987d3658c89ffda83bdac4d2cff910b2fc075d5c0c26248746fe46`.

O manifesto registra branch, HEAD recebido, worktree suja e hashes dos inputs
de código/scripts. HEAD sozinho não identifica as alterações novas. Não há
`release-manifest` aceito pelo agente, assinatura, roots fabricadas, config,
identidade, token ou scripts de instalação dentro do ZIP. Não é RC46 Pilot e não
foi submetido a instalador, servidor ou publicador.

Para reproduzir o artefato local, depois dos testes: configurar o SDK no PATH e
executar `scripts/New-NightOwlDevelopmentArtifact.ps1`. O gerador não modifica
versão no csproj e escreve em diretório único de artifacts. Metadados/data do ZIP
não prometem reprodutibilidade byte a byte; source inputs, SDK e hashes tornam a
composição auditável. Não substituir o builder oficial por esse ZIP.

No ambiente autorizado, para preparar Pilot: provisionar roots públicas e chaves
protegidas por canal local, usar o builder oficial com versão nova e canal pilot,
testes ligados, sem Publish e sem AllowUnsignedDevelopment; validar ZIP, manifesto,
hashes, assinatura, trust e permissões. Não executar publicador DryRun nesta
máquina: ele também exige configuração de release. Publicação/importação futura,
mesmo pausada/rollout zero, exige aprovação separada; não sobrescrever RC45/stable.

Homologação futura em VM: snapshot limpo, instalação autorizada, identidade e
autenticação preservadas, heartbeat/inventário/job ACK, update assinado com Tray
aberto, quiesce/handshake, rollback por falha, repair e receipt, uninstall com
autorizações/ACK, reinício e reconciliação após crash. Restaurar snapshot entre
cenários. Preservar backup validado e journals; resultado desconhecido nunca
autoriza reinstalação cega. Não usar este ZIP unsigned nessa homologação.

## Pendências e reprodução futura

| Nome/gate não homologado | Dependência e risco | Como executar futuramente |
| --- | --- | --- |
| PostgreSQL de automações, delivery/start/result/identidade/rollout | Instância descartável ausente; SQLite não prova locks, races ou migrations PostgreSQL | Provisionar loopback `nightowl_rc46_isolated`, definir NIGHTOWL_TEST_POSTGRES_URL por canal local e executar settings_test_automations_postgres; expandir os módulos listados no apêndice |
| Tray real/SCM/múltiplas sessões | VM instalada e sessão interativa autorizada; risco em logon/restart/ACL | Testar serviço pending, Running, ausente e query negada; restart menor/maior que 120 s; sessões/mutex; leitores durante rotação; update/uninstall com Tray aberto |
| Lifecycle assinado / MSI / reboot | VM, catálogo/aprovações e trust de release; risco em integridade/recovery/resultado incerto | Roteiro Pilot acima e matriz negativa de hash/assinatura, ACL, timeout, child vivo, reconexão e ACK perdido |
| Carga 100 agentes/WSGI/proxy/TLS | Laboratório isolado com proxy/backend reais | Medir sinal, delivery/start/ACK, sockets, CPU/RSS/IO e backlog sob reconexão/offline; não extrapolar loopback |
| Disco realmente cheio, power cut e ACLs sob contas de serviço | Volume/VM descartável para fault injection | Preencher somente volume descartável e testar rotação, crash durante rename/write, recovery e leitores; erro disk-full local foi apenas injetado no ponto de escrita |
| Política de exportação/evidência e legado na frota | Inventário e decisão operacional | Revisar retenção antes de Pilot, executar inventário read-only autorizado, confirmar identidade/heartbeat/inventário/ACK e aplicar aposentadoria individual com rollback |
| Builder/Pilot | Roots/chave aprovadas ausentes | Builder oficial com material provisionado; nunca criar raiz substituta para fazer passar |

Os 31 skips da rodada ampla compreendem 29 gates PostgreSQL e dois browser opt-in.
O browser de automações foi reexecutado com sucesso nos 89 testes. O gate de
fleet UI também foi reexecutado separadamente: 1 teste PASS em 2,964 s. Restam
29 gates PostgreSQL não executados, listados no apêndice. Nenhum skip foi
alterado no código.

## Pastas geradas e restrições de limpeza local

Três tentativas de artefato que falharam deixaram diretórios próprios em
`artifacts/rc46-development`: `20261030T144532Z-98d8a9e06d974334b6822a105037b9b4`,
`20261030T144940Z-8b1c8febfb104074af55a6af61eb969c` e
`20261030T145108Z-8c6fb0b7d810463e9f2c7b78187f848a`, totalizando 1.102.560.211 B.
Sua remoção, mesmo validada dentro do diretório de artifacts e sem tocar o
artefato verificado, foi rejeitada pela revisão automática (`blocked by policy`).
Permanecem ignoradas pelo Git e não são endpoints, release publicada ou dados de
produção. O bloqueio também impediu remover a pasta antiga vazia dentro de NetStudy.
Nenhum contorno dessa rejeição foi tentado.

## Decisão final e continuidade

Escopo local de código fechado conforme `rc46-agent-final-closure.md`, com
preservações e pendências operacionais explícitas. RC46 permanece **NO-GO para
produção, publicação e instalação**. Revisar diff, retenção e evidências; fechar
PostgreSQL/laboratório/trust/Pilot; obter aprovação específica antes de retomar
DEC-04C. DEC-04C/04D não foram iniciadas. Não houve commit/push, merge, instalação,
publicação, GPO, acesso corporativo ou migrations em produção.

## Comandos-base de reprodução local

```powershell
$env:DOTNET_ROOT = "$env:LOCALAPPDATA\NightOwlDev\dotnet"
$env:PATH = "$env:DOTNET_ROOT;$env:PATH"
$env:DJANGO_SECRET_KEY = 'isolated-rc46-test-key'
$env:DJANGO_DEBUG = 'True'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:AD_AUTH_ENABLED = 'False'
$env:AD_COMPUTER_DISCOVERY_ENABLED = 'False'
$env:NIGHTOWL_JOB_AUTOMATIONS_ENABLED = 'False'
$env:NIGHTOWL_TELEMETRY_LIVE_CACHE = Join-Path $env:TEMP 'nightowl-rc46-local-live'

# Build precisa terminar antes dos testes E2E que carregam as mesmas DLLs.
Get-ChildItem -Directory -Filter 'NightOwl.Agent.*' | ForEach-Object {
    Get-ChildItem -LiteralPath $_.FullName -Filter '*.csproj' | ForEach-Object {
        dotnet build $_.FullName -c Release --nologo
        if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    }
}
dotnet run --project NightOwl.Agent.Shared.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --repair-only
dotnet run --project NightOwl.Agent.Updater.Tests -c Release --no-build
dotnet run --project NightOwl.Agent.Uninstaller.Tests -c Release --no-build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-NightOwlLifecycleLogRetention.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-NightOwlLegacyInventory.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-NightOwlDiagnosticsFixture.ps1
.venv/Scripts/python.exe manage.py check --settings=config.settings_test
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.settings_test
.venv/Scripts/python.exe manage.py test agents config dashboard.test_agent_install_workflow dashboard.test_remote_install_enrollment dashboard.test_capacity_dashboard --settings=config.settings_test --noinput
git diff --check
```

Browser: localizar o Playwright desta máquina, definir NODE_PATH correspondente,
PLAYWRIGHT_CHANNEL=chrome e NIGHTOWL_TEST_BROWSER_E2E=1; usar os oito módulos
do comando do handoff com `config.settings_test_browser`. Não copiar caminhos
do host antigo. Para fleet UI, NIGHTOWL_UI_TESTS=1 e NIGHTOWL_BROWSER_CHANNEL=chrome.


## Apêndice — testes PostgreSQL não executados

Todos dependem de PostgreSQL descartável com locking real. Risco e procedimento
seguem a matriz acima; não executar estes módulos com settings de produção.

- `agents.test_automation.AutomationPostgresTests.test_two_scheduler_instances_one_occurrence_per_endpoint`
- `agents.test_enrollment_identity.PostgreSQLEnrollmentIdentityTests.test_simultaneous_first_enrollment_cannot_duplicate_or_rotate_identity`
- `agents.test_job_receipt_postgresql.PostgreSQLReceiptProgressionTests.test_two_late_rollback_replays_append_once`
- `agents.test_rollout_campaigns.PostgreSQLCampaignConcurrencyTests.test_concurrent_creation_and_restart`
- `agents.test_rollout_campaigns.PostgreSQLCampaignConcurrencyTests.test_restart_with_250_targets`
- `agents.test_rollout_control.PostgreSQLControlTests.test_admin_concurrent_start_pause_abort_resume_abort`
- `agents.test_rollout_control.PostgreSQLControlTests.test_known_error_isolated_and_empty_round_no_audit`
- `agents.test_rollout_control.PostgreSQLControlTests.test_three_waves_rounds_pause_resume_abort_and_capacity`
- `agents.test_rollout_control.PostgreSQLControlTests.test_two_runners_and_lock_release_after_errors_and_restart`
- `agents.test_rollout_dispatch.PostgreSQLDispatchTests.test_concurrent_dispatch_repeated_and_restart`
- `agents.test_rollout_dispatch.PostgreSQLDispatchTests.test_legacy_get_concurrent_with_campaign_dispatch_creates_only_campaign_job`
- `agents.test_rollout_dispatch.PostgreSQLDispatchTests.test_manual_writer_and_campaign_both_lock_orders`
- `agents.test_rollout_dispatch.PostgreSQLDispatchTests.test_migration_reverse_before_and_after_dispatch`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_auto_pause_serializes_with_human_pause`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_concurrent_wave_and_campaign_completion_are_idempotent`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_dispatch_commit_before_governance_can_create_only_pre_pause_job`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_governance_commit_before_dispatch_creates_zero_post_pause_jobs`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_governance_serializes_with_human_abort`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_migration_0033_backfill_guards_reverse_and_reapply`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_observation_completion_serializes_with_abort`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_two_auto_pause_calls_are_idempotent`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_two_governance_runners_and_restart_safety`
- `agents.test_rollout_governance.PostgreSQLRolloutGovernanceTests.test_wave_completion_serializes_with_human_wave_pause`
- `agents.test_rollout_planning.PostgreSQLPlanningRestartTests.test_plan_survives_connection_restart`
- `agents.test_rollout_reconcile.PostgreSQLRolloutReconcileTests.test_result_write_race_converges_on_next_round`
- `agents.test_rollout_reconcile.PostgreSQLRolloutReconcileTests.test_two_reconcilers_produce_one_transition_and_audit`
- `agents.test_stale_job_postgresql.PostgreSQLStaleRunningConcurrencyTests.test_completion_wins_and_timeout_rechecks_after_lock`
- `agents.test_stale_job_postgresql.PostgreSQLStaleRunningConcurrencyTests.test_timeout_wins_and_late_http_result_cannot_reopen`
- `agents.test_stale_job_postgresql.PostgreSQLStaleRunningConcurrencyTests.test_two_timeout_reconcilers_apply_and_audit_once`
