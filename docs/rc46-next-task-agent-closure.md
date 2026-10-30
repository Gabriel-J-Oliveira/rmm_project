# RC46 - Proxima tarefa: encerrar limpeza do agente Windows

## Entrada e autorizacao

Ler primeiro `rc46-handoff-new-machine.md`, `rc46-agent-cleanup-final.md`, `rc46-agent-cleanup-phase1.md` e relatorios DEC-01/02/03/04A/04B/capabilities nesta pasta.

Continuar em `feature/rc46-agent-core` ou worktree derivada e explicitamente identificada. Confirmar HEAD remoto, status e arquivos locais antes de alterar. Snapshot integrado: `901898c1e27f2137d4a6f51bb13dd226601bf8db`; ponta de handoff inclui a documentacao posterior. Preservar todos os incrementos, identidade, enrollment, criptografia, resultados pendentes, ownership, assinatura e rollback.

Escopo de desenvolvimento: **encerramento da limpeza do agente antes da DEC-04C**. Nao autoriza refatorar backend/frontend; somente testes de contrato estritamente necessarios. Nao autoriza commit/push adicional, merge, deploy, publicacao, assinatura com chaves reais, alteracao de stable, instalacao ou operacao em endpoints sem aprovacao especifica. Preparar pacote Pilot nao e permissao para publica-lo.

## 1. Reproduzir baseline

Usar os comandos do handoff. Build Release dos dez projetos; Shared, Windows focados, Updater via apphost/dotnet run, Uninstaller, Diagnostics, Django contratos e Node. Registrar versoes e assercoes. Nao confundir a invocacao incorreta `dotnet Updater.Tests.dll` com regressao do mutex.

A suite Windows completa falha em `TestRepairRunnerScriptPersistsCompletedResult`, `Repair runner pending result should be completed.` A falha foi reproduzida na base limpa e preservada. Nao pular, remover ou mudar a expectativa para aprovar gate.

## 2. Rotacao e retencao dos logs

Inventariar JsonlLogger, logs Shared/path, Tray, updater/repair, installer/uninstaller e Diagnostics. Distinguir append nativo da rotacao NSSM legada. Leitura limitada do Tray ja existe; nao refazer.

Propor limites/retencao explicitamente antes de implementar exclusoes: tamanho por arquivo, numero de rotacoes, ownership, redacao, exportacao de evidencias, disco cheio, leitores concorrentes e reboot/crash. A proposta10 MiB x5/30 dias do relatorio nao e politica corporativa aprovada. Nao apagar resultados nao ACKados, config/identidade/trust, backups ainda necessarios ou evidencias de resultado incerto. Nao remover historico simplesmente para reduzir tamanho.

Testar rotacao concorrente com escrita/leitura, limite total, rename falho/ACL, disco cheio, sanitizacao e preservacao de dados essenciais. Documentar claramente o que ja foi implementado e o que requer aprovacao operacional.

## 3. Repair runner

Rastrear teste, script gerado, persistencia de PendingResultRecord e handoff para processo externo. Reproduzir isoladamente com ambiente/cultura/PowerShell corretos e registrar primeira assercao e causas sanitizadas. Corrigir a causa minima sem relaxar resultado completed, sem enfraquecer protecoes ou recovery. Adicionar regressao para sucesso, falha, estado incerto, restart/crash, ACL e fila indisponivel quando pertinente. Executar teste isolado e suites Windows/Updater/Shared reais, depois builder no modo local apropriado e aprovado, sem publish e sem SkipTests.

## 4. Arquivos, processos e legado

Inventariar residentes reais, runners temporarios, tarefas, filas, journal, cache/pacotes, staging, backups e diagnosticos. Identificar dono e contrato de cada caminho. Pendentes legados nao criticos tem politica de prune diferente dos criticos V2: avaliar migracao compativel sem expurgo destrutivo.

Preparar roteiro somente leitura para detectar NightOwlAgent/NSSM e RMM-Agent-Heartbeat, validar identidade forte e migracao .NET, confirmar heartbeat/inventario/job ACK, preservar definicoes para rollback. Nao desativar legado em massa, nao apagar C:\RMM e nao mexer em GPO. Ausencia de inventario da frota e pendencia operacional, nao justificativa para remover compatibilidade.

## 5. Configuracao e dependencias residuais

Usar a matriz do relatorio final. Nenhuma configuracao publica foi eliminada; defaults de configs antigas continuam telemetria desabilitada/legacy, enquanto novas usam continuous. Preservar aliases e configuracao explicita. Revisar parametros/caminhos residuais somente com consumidores comprovados, sem ampla reescrita.

Nao remover bibliotecas, reflection, trust, runtime ou suporte de repair para economizar bytes. Medir build, pacote self-contained e instalacao separadamente; medir CPU/RSS/IO em Windows isolado. Benchmark de arquivo nao e reducao do tamanho instalado.

## 6. Lifecycle e RC46 Pilot

Preparar testes em VMs descartaveis: instalacao, re-enrollment legitimo, update assinado, quiesce, substituicao, handshake, rollback, repair, uninstall e receipts/ACK. Testar reconexao, timeout, processo iniciado com resultado desconhecido e permissao negada; nunca reinstalar cegamente.

Pacote Pilot deve ser imutavel, gerado pelo fluxo oficial com testes, hashes e assinaturas aprovados. Nao sobrescrever RC45, nao promover stable, nao publicar nem iniciar rollout. Planejar release pausada/rollout zero quando autorizada, canario unico e rollback. Chaves de assinatura nao ficam no Git nem no chat.

PostgreSQL descartavel, carga sustentada100 agentes/WSGI/proxy/TLS e MSI assinado continuam gates separados; nao substituir por SQLite ou stub.

## 7. Tray real e criterios de aceite

Homologar em Windows descartavel: logon com servico Running/pending; restart curto e longo; limite monotonico de120 s; ausencia/consulta negada do servico; varias sessoes e mutex; update/uninstall com Tray aberto; leitores durante rotacao; refresh sem criacao de identidade/config e sem logs Running repetidos. Nao redesenhar UI ou iniciar DEC-05 inteira sem aprovacao.

Comparar antes/depois: CPU/RSS, startup, heartbeat, inventario, IO, logs, residentes, trafego e tamanho instalado. Separar medicao real, fixture e estimativa. Reexecutar suites e git diff --check, registrando cada gate NAO EXECUTADO.

Entregar diff por lote, testes, politica de retencao aplicada versus proposta, diagnostico do repair, plano de legado e lifecycle/Tray, pacote apenas preparado quando autorizado e decisao honesta parcial/concluida/bloqueada. **Nao iniciar DEC-04C automaticamente.**
