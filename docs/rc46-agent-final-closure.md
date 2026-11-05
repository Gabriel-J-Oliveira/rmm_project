# RC46 — Encerramento final da simplificação do agente Windows

Data: 2026-10-30. Branch `feature/rc46-agent-cleanup-closure`.
**Revisão local encerrada; homologação/produção continuam NO-GO.**
DEC-04C pode ser retomada em uma tarefa específica sem nova revisão geral de
limpeza. Não foi iniciada aqui. Isso não elimina gates de instalação assinada,
Tray/SCM/sessões reais, carga, TLS/proxy e autorização de publicação.

Referências complementares: `rc46-agent-footprint-report.md` e
`rc46-pilot-windows-validation.md`. Histórico de onboarding/testes da primeira
entrega: `rc46-new-machine-validation.md` e commits registrados abaixo.

## Política de persistência aplicada

Logs técnicos: ativo até 10 MiB + cinco rotações, expiração das rotações em 30
dias na próxima escrita e registro até 64 KiB após redaction. Defaults de
desenvolvimento sujeitos à aceitação operacional antes de rollout, não política
corporativa presumida. Caminhos exatos e sufixos numerados, sem varredura genérica.
Arquivos legados já maiores são preservados inteiros na rotação; 60 MiB é limite
para novos logs por caminho, não redução imediata nem quota de todos os dados.

Filas de resultado: default 1.000 registros/128 MiB, payload até 256 KiB;
resultados sem ACK não são expurgados por idade/quota. Quarentena ocupa o mesmo
budget e precisa de reconciliação, sem expurgo para liberar artificialmente
capacidade. Recibos sent: 1.000/14 dias desde ACK. Identidade, trust, checkpoints,
outcomes incertos, backup de rollback e evidência de runner continuam protegidos.
Temporários são limpos pelo fluxo dono/estado, sem apagar Temp/ProgramData gerais.

Logs por usuário são preservados no uninstall, como evidência, com quota própria.
Não há varredura de outros perfis ou novo daemon de limpeza. A projeção do Tray
é pequena, fixa, atomic e sem credenciais; Config/State mantêm ACL admin-only.

## Baseline, branch e commits

Recebido `6fe53cac4f064013fa6993b8c4c56041450d26b9`, inicialmente com as mudanças
locais da primeira entrega preservadas. Branch de trabalho continua
`feature/rc46-agent-cleanup-closure`. `feature/rc46-agent-core` continua no SHA
recebido e não foi alterada. Criado checkout detached desse SHA para comparação
de pacote, sem mudanças de código produtivo. Não houve push, merge ou operação
corporativa. SHA final de código Windows: `7b8ce4f9af808b638acb9e75f9609427258cb4e2`;
a documentação final é commitada separadamente, e `git rev-parse HEAD` identifica
a ponta completa entregue.

| Commit local | Responsabilidade |
| --- | --- |
| 2ee84f3 | Retenção de logs e preservação de resultados sem ACK da primeira entrega |
| 6650321 | Quoting nativo e regressões de repair/lifecycle |
| b68cb7a | Evidências da primeira entrega, inventário legado e artefato de desenvolvimento |
| f4e531b | Recovery de log parcial, redaction e quota compartilhada da quarentena |
| 026fa4b | Preservação da configuração no installer/repair, campos futuros e telemetria |
| cbf0118 | Fronteira de leitura do Tray, projeção pública e testes do Worker |
| 3f9b19f | Comparação com baseline e smoke de processo/sessão do Tray |
| 7b8ce4f | Logs interativos por usuário, sem ampliar ACL de ProgramData |
| 7c3f0a2 | Isolamento de schema PostgreSQL e sincronização do browser de integração |

## Problemas adicionais corrigidos

1. **Installer descartava configuração.** Apenas três opções de telemetria eram
   mantidas; modo continuous/quotas, jobSignalSeconds, allowlist reduzida, pin,
   flags, cadências e outros campos eram sobrescritos/perdidos. O helper comum
   `Merge-AgentRuntimeConfig` atende checkpoint e configuração final. Identidade,
   bearer, versão e installPath continuam resolvidos pelo fluxo de lifecycle;
   parâmetros explícitos existentes são preservados. Um ServerUrl explicitamente
   alterado usa as rotas novas em vez de manter endpoints do servidor anterior.
2. **Servidor existente era resolvido depois do download.** Agora configuração e
   servidor preservados são resolvidos antes de escolher a URL do pacote. O valor
   default do parâmetro não equivale a uma escolha explícita para substituir a
   configuração existente. Não foi criado novo fallback de autenticação.
3. **ConfigService eliminava campos desconhecidos ao persistir o modelo.**
   AgentConfig agora preserva extension data, como AgentState já fazia. Isso não
   habilita jobs/flags desconhecidos: consumidores tipados continuam autoritativos.
4. **Tray fazia bootstrap privilegiado no logon, antes do mutex.** Removido.
   Serviço/installer continuam donos da migração. Instâncias duplicadas e o usuário
   interativo não criam Config, Identity, State ou diretórios de update.
5. **Tray dependia da leitura de Config/State admin-only.** A projeção
   `Logs/tray-status.json` contém somente schema, machine/endpoint ID, versão,
   servidor sem userinfo/query/fragment, caminhos de exibição e último heartbeat.
   Escrita atomic após persistência de estado, apenas se mudou/precisa recuperar;
   falha da projeção não bloqueia estado essencial. Leitura do Tray é limitada e
   não precisa abrir config com bearer. Snapshot inválido/oversized usa fallback.
6. **Tray tentava escrever em Logs onde Users têm RX.** Logging normal vai para
   LocalAppData do usuário, com rotação e redaction. Diagnostics inclui somente
   o log do usuário corrente e o log de máquina histórico, sem varrer outros
   perfis. NIGHTOWL_HOME explícito mantém fixtures/overrides no root indicado.
7. **PDH era inicializado mesmo sem amostragem nativa.** A query abre apenas na
   primeira leitura solicitada. Perfil desabilitado não inicia o pipeline; modo
   desconhecido gera diagnóstico e não se transforma silenciosamente em legacy.
8. **Log parcialmente gravado podia engolir o evento seguinte.** Fragmento é
   preservado e separado com newline antes da próxima entrada, respeitando a
   quota. Testes injetam escrita parcial; não é uma simulação de power cut físico.
9. **Quarentena de resultados podia crescer fora da quota.** Seus arquivos agora
   contam nos limites de registros/bytes da fila. Capacidade ocupada faz
   backpressure, sem destruir evidência corrompida. Nomes únicos evitam overwrite.
10. **Fixture do contrato remoto usava PSHOME/powershell.exe no PowerShell 7.**
    Corrigida para o Windows PowerShell 5.1 real usado pelo contrato do filho;
    testes passam nos dois hosts sem skip ou relaxamento.
11. **Fixtures PostgreSQL antigas deixavam o schema na migration 0033.** Dois
    testes de rollback agora restauram todos os leaf nodes via addCleanup, mesmo
    após falha, e verificam a tabela de live telemetry. As asserções de rollback
    inseguro continuam intactas. Sem isso, a integração .NET/telemetria seguinte
    falhava com UndefinedTable. Alterados somente esses dois testes Django.
12. **Browser de automações submetia enquanto o refresh de edição estava ativo.**
    O harness agora espera a substituição da linha e confirma ID/revision antes
    de submeter. A expectativa de r2, persistência e counts continua a mesma;
    nenhum retry/skip/timeout ampliado ou mudança no JS produtivo foi introduzido.

Código removido/consolidado nesta revisão: bootstrap do Tray, append após
fragmento sem separação, inicialização eager de PDH, reconstrução destrutiva de
opções de config e segunda limpeza do pacote no caminho de sucesso do installer.
A limpeza no finally permanece. Não foram removidos arquivos de compatibilidade,
DLLs/runtime ou métodos públicos por preferência de estilo. Nenhum backend,
frontend, contrato remoto produtivo ou migration foi refatorado.

## Configuração e equivalência limpa/atualizada

Teste de helper real PowerShell seguido do loader/normalizador .NET verifica
preservação de campos e equivalência de uma instalação limpa com atualização
sem mudança de perfil. Testados allowlist explicitamente reduzida e migration
version 6, modo continuous explícito, quotas, cadências, flags e servidor/rotas.
Essa equivalência é de configuração funcional; não afirma que registro SCM,
instalação de binários e logon tenham sido homologados numa VM.

Valores operacionais foram preservados: installer novo continua enabled=true,
perfil efetivo legacy agora explícito, sample/flush 300/900, heartbeat 300,
collect 3600 e jobs legado 10 quando sem resposta do enrollment. Modelo .NET
criado diretamente mantém continuous/true e jobs legado 45; são origens diferentes,
agora documentadas. Config antiga sem flag permanece disabled/legacy e flush
3600. Perfil continuous 10/60 e jobSignal default 2 mantêm seu contrato; campos
sample/flush são do perfil legacy e não passam a controlar o modo contínuo.

## Matriz de legado e decisão de manutenção

| Grupo legado | Substituição atual e decisão |
| --- | --- |
| RmmAgent.ps1 | Wrapper compatível de uma iteração; chamado pela tarefa/check/deploy. Mantido |
| RmmAgentService.ps1 | Heartbeat, inventário e jobs possuem equivalentes .NET; não há prova de frota toda migrada. Mantido para endpoints antigos |
| Install/Uninstall-RmmAgent.ps1 | Tarefa, configuração, manual validation e upgrade antigos têm consumidores nas ferramentas legadas. Mantidos |
| Install/Uninstall-RmmAgentService.ps1 / NSSM | Serviço NightOwlAgent e fallback da tarefa; não são runtime obrigatório do .NET. Mantidos até aposentadoria por endpoint |
| Update/Check/Deploy-RmmAgent.ps1 | Referências reais no manifest e nos próprios fluxos de versões antigas. Mantidos |
| NightOwlManualValidation.ps1 / assets | Consumidos pelo enrollment/instalador legado. Mantidos, não copiados ao ZIP .NET |
| Exemplos PS1/JSON, VERSION, manifest e README | Contratos/documentação do distribuidor legado. Mantidos |
| C:\RMM e Agent/AgentDotNet antigos | Fontes de identidade/estado/pending; sem expurgo etário. Reconciliar antes de aposentar |
| KeepPowerShellAgent / DisablePowerShellAgent / KeepScheduledTaskFallback / WinswPath | Contratos externos e coexistência; argumentos conhecidos ainda são encaminhados por scripts. Mantidos |
| GPO/WinRM/bootstrap | Preparação de instalação, não requisito do ciclo normal outbound .NET. Sem alteração operacional |
| scripts/powershell/access_inventory | Inventário AD/ACL separado; não é agente endpoint obsoleto |

Não foi demonstrado arquivo legado sem consumidor/dependência relevante que
permitisse remoção segura. O plano concreto por endpoint, seus gates de identidade,
heartbeat/inventário/job ACK, preservação de definições, GPO e rollback está em
`rc46-pilot-windows-validation.md`. Coexistência pode duplicar trabalho; isso é
um gate operacional explícito, não permissão de desativar a frota nesta tarefa.

## Lifecycle, repair e Tray

Fluxo revisto: configuração/migração/identidade antes da escolha do download,
trust/hash/assinatura antes de instalação, backup/quiesce, substituição, serviço,
tarefa/atalho, health, state/receipt e cleanup finally. Updater mantém handshake,
ownership e rollback. Uninstaller mantém autorização/receipt e proteções de path.
Resultados incertos, backup e filas não são descartáveis.

Repair continua PASS na suíte completa e no gate do builder: sucesso, falha do
installer, fila indisponível, argumentos/caminhos e recuperação/ownership. Não
foi instalado/reparado o serviço local encontrado Stopped.

Tray pronto em código para homologação: sem bootstrap, mutex por sessão, grace
monotônico, leitores limitados, projeção sem credencial, logs por usuário, recursos
WinForms descartados. Smoke real de nova instância confirmou duplicate-exit na
sessão 2, em 181 ms, sem diretórios protegidos. Não houve reinício de Explorer ou
serviço real; múltiplas sessões, update/uninstall com Tray aberto e GUI instalada
continuam pendências de homologação. Não houve redesign visual.

## Evidência final

- Build dos dez projetos Release: PASS, zero warnings/errors nos gates finais.
- Shared, Windows completo, Updater e Uninstaller: PASS, inclusive pelo builder
  final com testes ligados. Novos contratos de projeção e retenção incluídos.
- Helpers/config/lifecycle/trust/ACL/contrato remoto/hardware: PASS em PowerShell
  5.1 e 7. O erro intermediário da fixture PS7 foi corrigido, sem skip.
- Django amplo: 717 testes, 60,937 s, 31 skips (29 PostgreSQL + dois browsers opt-in).
- Contratos com Chrome/live/jobs/DEC-04B: 40 testes, 36,760 s, um skip PostgreSQL;
  browser de automações foi executado. Os testes opt-in anteriores também estão
  registrados na primeira entrega. Logs ERROR de cenários negativos esperados não
  são resultados de produção nem falha da suíte.
- **PostgreSQL 16.15 real, rodada final:** 717 testes em 143,662 s, **OK sem skips**,
  incluindo os 29 gates de banco e ambos os browsers opt-in. Instância autenticada
  SCRAM em 127.0.0.1, criada só para testes; migrations aplicadas exclusivamente
  nessa base descartável. Test databases removidos e servidor próprio encerrado.
- Check/makemigrations dry-run: PASS; nenhuma mudança backend/migration.
- Diagnostics fixture: PASS; confirmação final após ajuste de log de usuário
  registrada em `artifacts/rc46-final-review/diagnostics-verified.log`.
- Builder oficial: testes e cinco publishes locais passaram, depois BLOCKED por
  RELEASE_TRUST_ROOTS_MISSING. Não houve assinatura fabricada, SkipTests ou Publish.
- Artefatos unsigned de baseline e código final: 512 arquivos cada, todos os
  hashes e source inputs verificados independentemente. Payload +10.240 B
  (+0,00597%), ZIP +4.355 B contra baseline; não se declara economia de pacote.
- Worker nativo local: 30,328 s, CPU normalizada 0,245%, working set máximo
  63.684.608 B; contexto e limitações em `rc46-agent-footprint-report.md`.

Inventário dos arquivos alterados: `git diff --name-status 6fe53ca..HEAD`, separado
pelos commits acima. Relatórios detalhados e roteiro entregues no repositório.
Logs/ZIPs/builds permanecem ignorados, sem publicação. Os resíduos de tentativas
antigas cuja remoção foi rejeitada continuam documentados em
`rc46-new-machine-validation.md`; esta rodada não tentou contornar essa rejeição.

## Aceite e continuidade

**Resolvido localmente:** revisão e simplificação do escopo Windows, problemas
acima e regressões executáveis. Não há duplicação conhecida sem decisão nesta
revisão; isso não é afirmação de inexistência absoluta de bugs ou redundâncias.

**Preservado com justificativa:** compatibilidade, runtime/satellites/DAC,
assinatura/trust, filas/recibos, estado incerto, backups, GPO e legado ainda
consumido. Nenhuma dependência de scripts/ConPTY foi retirada.

**Pendente de homologação:** instalação/upgrade/repair/uninstall assinados, contas
  SCM/ACL e sessões reais, Explorer, carga/proxy/TLS, falha física de
disco/power cut e inventário/aposentadoria da frota. Não classificados como PASS
ou como limpeza operacional concluída.

**Publicação RC46: NO-GO.** Falta trust/assinatura autorizadas e os gates de
homologação do roteiro. Não houve instalação, update de endpoints, produção,
push ou merge; DEC-04C/04D não foram iniciadas.

**DEC-04C pode começar numa próxima tarefa específica sem nova rodada geral de
limpeza.** A revisão local está encerrada com decisões explícitas; os gates de
Pilot/produção permanecem independentes e falhas futuras terão correção pontual
e regressão. Essa conclusão não inicia a DEC nem autoriza contornar homologação.


## PostgreSQL: resultado final e reprodutibilidade

PostgreSQL portátil 16.15 foi preparado nesta máquina, autenticado por SCRAM,
bind 127.0.0.1 em porta dinâmica, sem serviço Windows. A configuração de teste
existente exige explicitamente loopback e banco `nightowl_rc46_isolated` e não
tem fallback de produção. Fonte: [PostgreSQL Windows](https://www.postgresql.org/download/windows/)
e [EDB ZIPs](https://www.enterprisedb.com/download-postgresql-binaries). Versão/hash
do download e lifecycle estão em `artifacts/rc46-final-review/postgres-tooling.json`
e `postgres-lifecycle-verification.json`, sem URL/senha exportada.

Execuções transparentes: primeira rodada 717/161,426 s falhou por schema legado
não restaurado; segunda 717/192,106 s passou esse ponto e encontrou a corrida de
sincronização do browser de teste. Após os fixes mínimos, quatro testes de
migração/telemetria/browser passaram em 30,129 s, e a rodada final completa foi
**717 testes / 143,662 s / OK sem skips**. Sem remoção de asserções ou alteração
de código produtivo Django/frontend. Restam gates de deploy/carga/Windows
instalado; PostgreSQL funcional local deixou de ser uma pendência não executada.

Para repetir, iniciar um cluster descartável próprio com SCRAM, bind loopback e
banco com o nome acima; definir NIGHTOWL_TEST_POSTGRES_URL em ambiente local
protegido, sem imprimi-lo. Habilitar NIGHTOWL_TEST_BROWSER_E2E=1,
NIGHTOWL_UI_TESTS=1, PLAYWRIGHT_CHANNEL=chrome, NIGHTOWL_BROWSER_CHANNEL=chrome e
NODE_PATH do Playwright local. Usar o comando amplo já documentado substituindo
os settings por `config.settings_test_automations_postgres`. Encerrar somente a
instância criada após os testes. Nenhum processo/serviço PostgreSQL alheio foi
alterado. O cluster desta rodada foi parado e havia zero bases de teste restantes.

## Arquivos desta entrega

Lista acumulada contra o SHA recebido; arquivos gerados/SDK/ZIPs não estão no Git.

- `NightOwl.Agent.Diagnostics/Program.cs`
- `NightOwl.Agent.Shared.Tests/LocalFootprintTests.cs`
- `NightOwl.Agent.Shared.Tests/Program.cs`
- `NightOwl.Agent.Shared.Tests/RetentionClosureTests.cs`
- `NightOwl.Agent.Shared/JobState.cs`
- `NightOwl.Agent.Shared/NightOwlPaths.cs`
- `NightOwl.Agent.Shared/RotatingJsonlLog.cs`
- `NightOwl.Agent.Shared/TrayRuntimeStatus.cs`
- `NightOwl.Agent.Tray/AgentLocalState.cs`
- `NightOwl.Agent.Tray/Program.cs`
- `NightOwl.Agent.Tray/TrayLog.cs`
- `NightOwl.Agent.Uninstaller/Program.cs`
- `NightOwl.Agent.Updater/Program.cs`
- `NightOwl.Agent.Windows.Tests/LightweightHeartbeatTests.cs`
- `NightOwl.Agent.Windows.Tests/Program.cs`
- `NightOwl.Agent.Windows.Tests/TelemetryConfigurationTests.cs`
- `NightOwl.Agent.Windows.Tests/TrayProjectionTests.cs`
- `NightOwl.Agent.Windows/Collectors/NativeDiskCounters.cs`
- `NightOwl.Agent.Windows/Jobs/JobExecutor.cs`
- `NightOwl.Agent.Windows/Models/AgentConfig.cs`
- `NightOwl.Agent.Windows/Services/ConfigService.cs`
- `NightOwl.Agent.Windows/Services/JsonlLogger.cs`
- `NightOwl.Agent.Windows/Services/StateService.cs`
- `NightOwl.Agent.Windows/Services/TelemetryPipeline.cs`
- `NightOwl.Agent.Windows/scripts/Install-NightOwlAgentDotNet.ps1`
- `NightOwl.Agent.Windows/scripts/Test-NightOwlRemoteInstallerContract.ps1`
- `NightOwl.Agent.Windows/scripts/Uninstall-NightOwlAgentDotNet.ps1`
- `agents/test_rollout_dispatch.py`
- `agents/test_rollout_governance.py`
- `docs/rc46-agent-final-closure.md`
- `docs/rc46-agent-footprint-report.md`
- `docs/rc46-new-machine-validation.md`
- `docs/rc46-pilot-windows-validation.md`
- `scripts/Build-NightOwlAgentRelease.ps1`
- `scripts/Get-NightOwlLegacyInventory.ps1`
- `scripts/New-NightOwlDevelopmentArtifact.ps1`
- `tests/Test-NightOwlInstallerConfiguration.ps1`
- `tests/Test-NightOwlLegacyInventory.ps1`
- `tests/Test-NightOwlLifecycleLogRetention.ps1`
- `tests/Test-NightOwlTrayStartup.ps1`
- `tests/frontend/test_job_automations_e2e.cjs`
