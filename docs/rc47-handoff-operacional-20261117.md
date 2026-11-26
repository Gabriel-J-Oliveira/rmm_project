# NightOwl RMM — Estado operacional, processos e handoff (17/11/2026)

> **Snapshot histórico de 17/11/2026 — não é o estado operacional atual e não autoriza deploy.** Para decisões RC49, consulte `docs/deploy-RC49.md` e `docs/operational-homologation.md`; revalide diretamente qualquer SHA, flag ou condição volátil. Atualizado a partir do histórico operacional fornecido pelo responsável e do repositório GitHub consultado em 17/11/2026 (BRT). Estados voláteis precisam ser novamente verificados antes de qualquer operação.
>
> **Repositório:** https://github.com/Gabriel-J-Oliveira/rmm_project  
> **Roadmap histórico:** [deploy-RC47.md](deploy-RC47.md)  
> **Processo de publicação:** [rc47-backend-deploy-readiness.md](rc47-backend-deploy-readiness.md) e [rc47-predeploy-homologation.md](rc47-predeploy-homologation.md)  
> **Prompt para continuação:** [rc47-prompt-continuacao-20261117.md](rc47-prompt-continuacao-20261117.md)

## 0. Resumo executivo — verdade operacional mais recente

| Camada | Evidência / estado em 17/11/2026 | Próximo gate |
| --- | --- | --- |
| GitHub `main` | `866afe400df532190088d3c5aa678e960f355c2b`; PRs #46, #48 e #49 integradas | Congelar SHA; **não** equiparar merge a publicação |
| Backend Linux de produção | `8467cd9541d5ce93e731a19f1789486abe898402` (hotfix #47 publicado em 16/11); checkout detached, somente `certs/` não rastreado | Deploy consolidado **ainda não executado** |
| Banco PostgreSQL | PostgreSQL 17.10; migrations `agents.0047`–`0050` já aplicadas; `migrate --plan` vazio | **Não** executar migration neste deploy |
| HTTP/proxy | `nightowl.service`, Nginx e PostgreSQL ativos, três workers Gunicorn | Backup e janela antes de reiniciar HTTP |
| Relay de terminal | `nightowl-terminal.service` inativo/desabilitado; porta 8765 sem listener; `admin_auth=false` | Homologação ConPTY **separada**, não iniciar no deploy |
| Gerenciador de Serviços | Backend #46 e allowlist #48 integrados na main, **não publicados**; flag default false, allowlist vazia | Backend desligado + nova release Windows assinada + piloto autorizado |
| Grants ConPTY | Gestão auditada #49 integrada, **não publicada**; allowlist de executores vazia; grant piloto antigo expirado | Deploy, executor autorizado, janela e consentimento na VM |
| Hotfix visual #47 | Publicado no SHA `8467cd…` e **homologado visualmente pelo operador** no CS-CVEL-0281: gráficos automáticos e alinhamento dos cards | Preservar comportamento e URLs versionadas |
| WinRM da VM | `TARGET_NOT_ALLOWED` esperado: VM fora do AD e sem FQDN válido | Não contornar AD nem testar recovery nessa VM |
| Jobs históricos | Um `uninstall_agent` vencido ainda `queued`; dois jobs de julho `sent` inconclusivos, todos num registro **antigo e offline** do CS-CVEL-0254 | Decisão humana de reconciliação, sem retry/reexecução |

**Decisão atual:** NO-GO operacional até haver decisão auditável sobre esses três jobs, janela comunicada/aprovada e checkpoint atualizado. O diagnóstico de jobs foi **somente leitura**. O NO-GO para retry/uninstall não equivale a afirmar que jobs vencidos serão despachados novamente: o código publicado filtra expiração, segundo a investigação.

## 1. Propósito e limites do produto

NightOwl é o RMM próprio da ControlSul, com backend Django/PostgreSQL no Linux e agente .NET em estações Windows. Foco atual: inventário, telemetria, gestão de jobs, políticas, releases assinadas, ações tipadas, monitoramento ao vivo, gerenciamento somente leitura de serviços e terminal assistido com consentimento.

Princípios: UUID e `machine_id` em vez de hostname isolado; nenhuma segunda fila/executor paralelo; autorização no frontend **e** backend **e** pull; informação ausente não é zero; ACK/idempotência e auditoria; jobs antigos sem resultado não devem ser refeitos automaticamente; nenhuma alteração de production por CI/merge.

**Fora deste ciclo:** gestão geral de Linux, novo agente Windows Server independente, integração com Zabbix/Wazuh/Bitdefender/AD e DLP. O estudo de Zabbix e Wazuh está em uma PR documental separada (#50) e não autoriza ações no NightOwl.

## 2. Arquitetura e ambiente real

- Servidor: `CS-HOST-INFRA`, IP de referência `192.168.106.51`; aplicação em `/opt/nightowl`, virtualenv `/opt/nightowl/.venv`, Python 3.13.5, Django, PostgreSQL 17.10.
- HTTP: `nightowl.service` como `nightowl`, Gunicorn/WSGI com três workers na porta local 8010; `DEBUG=False` e `cached.Loader` exigem restart para templates novos.
- Nginx serve `/static/` diretamente de `/opt/nightowl/staticfiles/`, `CompressedStaticFilesStorage`; `/ws/terminal/` encaminha para Uvicorn/ASGI em `127.0.0.1:8765`, que está inativo no momento.
- URL pública: https://nightowl.controlsul.com.br. Conferir TLS público com validação; origem interna possui certificado de `nightowl.control.local`. A política de validação TLS do upstream NPM permanece ponto de auditoria histórica.
- `nightowl-maintenance.timer` ativo a cada ~5 min, `nightowl-maintenance.service` executado historicamente como root. Coordenar com janela, registrar estado original e restaurá-lo; não habilitar automações adicionais.
- Produção preserva `certs/` (root:root 0700, não rastreado), `.env`, `.venv`, mídia, logs e backups; **nunca executar `git clean` ou alterações recursivas de ACL**.
- Espaço observado no preflight: ~3,1 GiB livres em `/` e ~128 GiB no volume de backups. `staticfiles/` em torno de 19 MiB. Há 385 sidecars `.gz` graváveis por `nightowl`, 27 arquivos estáticos sem escrita efetiva e `vendor`/`vendor/xterm` root:root, com conteúdo inalterado. Não aplicar `collectstatic --clear`.
- Migrations do banco até `agents.0050_telemetry_scheduler_health` estão aplicadas; nenhuma nova migration nas PRs #46, #48 e #49. Não assumir que rollbacks de migrations antigas são seguros.

## 3. Histórico de evolução e estado das PRs (distinção entre Git/produção)

| PR | Entrega | Estado |
| --- | --- | --- |
| #27–#33 | Tema endpoint/Lucide, normalização da telemetria, log JSONL, UX ConPTY e melhorias relacionadas | Integradas historicamente; features dependentes do Windows exigem bytes de release assinada |
| #40 | Políticas atribuídas por endpoint | MERGED; CI passou |
| #41 | Revalidação de update_agent no pull, concorrência PostgreSQL; rollout manual explícito 0% preservado | MERGED; sem publicação Windows por isso |
| #42 | RC47-09: histórico seguro de WinRM e ajustes da aba | MERGED; falha Chrome/CDP transitória sem causa comprovada |
| #43–#45 | Planos de pré-deploy, prontidão e restauração isolada | Documentação integrada; planos históricos, não autorização operacional |
| #46 | Gerenciador de Serviços Windows **somente leitura**, paginado (20 serviços/página, até 40 páginas/800) | MERGED em `69e5f48b…`; backend e agente atual não publicados |
| #47 | Hotfix do monitoramento vivo (seleção inicial automática) e padding Ações/Jobs; cache busting | MERGED em `8467cd9541d5ce93e731a19f1789486abe898402`; **publicado/homologado 16/11** |
| #48 | Allowlist global + UUID por endpoint para `collect_services`, cancelamento auditado de `queued` revogados | MERGED em `99d6a01052d4abce95df58f5d49db12fb762843e`; ainda não publicado |
| #49 | Comando auditado `terminal_conpty_grant`, grant ≤60 min e revogação de sessões | MERGED em `866afe400df532190088d3c5aa678e960f355c2b`; ainda não publicado |
| #50 | Estudo independente Zabbix/Wazuh | PR documental draft **separada**; não integra a publicação NightOwl |

**Diff para o próximo deploy:** `8467cd9541d5ce93e731a19f1789486abe898402` → `866afe400df532190088d3c5aa678e960f355c2b`, 27 arquivos, somente PRs #46/#48/#49. Sem alterações de dependências nem migrations. Inclui fontes C# da Fase A, mas **nenhum release Windows** faz parte deste deploy Linux. Dois assets novos: `endpoint_services.css` e `endpoint_services.js` com query `?v=services-phase-a-1`. Preservar o cache-buster `rc47-hotfix-47-1` da PR #47.

## 4. Identidades críticas — nunca escolher somente pelo hostname

**Dois dispositivos diferentes chamados CS-CVEL-0281:**

| Alvo | UUID | Machine ID | Contexto |
| --- | --- | --- | --- |
| VM piloto RC47 | `c97045f2-35ff-48f1-afa5-095173fbb3dd` | `1eca4af2-4950-4a2d-9b9f-fa7010a62464` | IP observado 192.168.104.65; fora do AD; hostname sem domínio; capability ConPTY em heartbeat; alvo do piloto de Serviços/Terminal |
| Dell Inspiron 3576 físico, RC45 | `1a46b467-5f83-418f-badd-41c8ffb6798b` | `ab0ad2c5-0f78-4170-a4e8-12ce081cc6cd` | IP observado 192.168.100.96; `CS-CVEL-0281.control.local`, objeto AD habilitado; candidato distinto para teste WinRM 5986 |

O AD e DNS correspondem ao **Dell físico**, não à VM. O `TARGET_NOT_ALLOWED` no histórico da VM é um bloqueio esperado antes de TCP/TLS/WS-Man. Não acrescentar `.control.local` artificialmente à VM. O teste WinRM existente pode verificar TCP/5986, TLS e Identify/challenge WS-Man; **não prova autenticação de credenciais**. Qualquer teste no Dell físico precisa de autorização própria.

## 5. Descrição operacional das novas funcionalidades

### Gerenciador de Serviços (PR #46 + #48)

- API e modal de consulta paginada somente leitura, pesquisa/filtros/ordenação/propriedades resumidas; jobs tipados `collect_services`, sem iniciar/parar/reiniciar/editar.
- Capability necessária no heartbeat do agente: `collect_services_paged:2`; **é preciso gerar e assinar nova release Windows** com versões inéditas, manifesto/hashes/trust e updater compatível; não substituir artefatos RC46/RC47 existentes.
- `NIGHTOWL_SERVICE_COLLECTION_ENABLED` default `False`; `NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS` vazia por padrão, comparando UUID integral. UUID de configuração inválido falha seguro; verificar **antes do restart**.
- O pull revalida flag, allowlist e identidade; revogação cancela/audita job ainda `queued`, mas não recolhe trabalho `sent/running`. Habilitação global sem allowlist não é permitida no piloto.
- Backend no próximo deploy deve permanecer desabilitado. Só depois da release assinada e identificação fresca da VM, autorizar habilitação específica e consulta real.

### Terminal ConPTY assistido (PR #49)

- Relay `nightowl-terminal.service` continua parado; flags assistidas relatadas verdadeiras, `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=false`. Grant do piloto ConPTY **expirado**; Tray foi reportado na versão RC47, mas usuário interativo e Tray rodando devem ser confirmados presencialmente.
- Comando novo: `/opt/nightowl/.venv/bin/python3.13 /opt/nightowl/manage.py terminal_conpty_grant`, com ações `status/grant/revoke`, prévia por padrão, `--apply` e confirmação para escrita, motivo e aprovação. Execução por usuário Unix autorizado em `NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS`, vazia por padrão; requer execução sudo rastreada. Ver [manual operacional](rc47-terminal-conpty-grant-operations.md).
- Grant ≤60 minutos; concessão exige usuário e endpoint elegíveis; revogação não depende dessa elegibilidade, nunca cria grants, preserva auditoria, fecha sessões e não altera `allow_admin_auth`. Grant que já tenha `allow_admin_auth=true` exige revisão separada.
- **Não iniciar relay, conceder grant ou abrir sessão no deploy consolidado.** Após publicação, em janela própria, confirmar Tray/sessão interativa, configurar executor autorizado, conceder grant temporário, `systemctl start` sem `enable`, testar WSS/consentimento, revogar e parar relay. Teste real de fechamento de processos ainda pendente.

### Telemetria, políticas, updates e RC47

- Monitoramento vivo e padding da #47 aprovados no navegador real em 16/11; gráficos de rede/disco surgem após primeira amostra válida, seleção automática e manutenção de escolha manual durante polling.
- O backend já possui análise persistida, políticas por endpoint, revalidação de jobs `update_agent` no pull e histórico de WinRM. Funcionalidades sujeitas a gates/desligadas não são automaticamente homologadas na frota.
- Atualização manual explícita pode usar release publicada com rollout 0%, mantendo gates de integridade/identidade/canal/pausa; atualizações automáticas continuam respeitando rollout. Não emitir jobs sem aprovação.
- Terminal administrativo `admin_auth` continua desabilitado. Não armazenar credenciais em navegador/DB/logs e não confundir admin_auth com consentimento assistido.

## 6. Incidente operacional atual: três jobs históricos no CS-CVEL-0254

**Registro antigo, offline**: UUID `da5d2b2f-3750-4af5-bd43-6635255fc7b5`, `machine_id=CS-CVEL-0254`, domínio CONTROL, agente 0.1.0; último heartbeat em **15/07/2026 13:21 UTC**.

**Registro diferente, online**: UUID `760f9d60-54b1-47b4-81b3-6a350a2db2b4`, `machine_id=70bceb8a-ea43-43d9-acd3-7c16025e4cb8`, agente `0.1.1.0-rc45`. Não atribuir os jobs do UUID antigo ao registro online.

| Job ID | Tipo/estado observado | Evidência | Tratamento proposto — ainda NÃO executado |
| --- | --- | --- | --- |
| `4ba2c049-6ff6-4174-89d6-5be91aefcccc` | `uninstall_agent / queued` | Criado 26/10 19:46 UTC por `gabriel.oliveira`; expiração 27/10 19:46 UTC; nunca despachado; request `e0ca2746-60ee-444d-88f6-0b337a309837` em `waiting_for_agent`, autorizado via painel | **Não reemitir**; decidir com operador se cancela via fluxo auditado do pedido, escolhendo UUID antigo, ou mantém até um pull que expire |
| `4a5725a1-fef5-4dfd-acc5-564dda113041` | `force_inventory / sent` | Despachado 09/07, expirado no mesmo dia; evento `job.started`, sem resultado/recibo terminal | Dry-run do reconciliador propôs `timed_out`; aplicar somente com motivo e aprovação, sem alegar que não executou |
| `8a5c3619-b955-481e-8f99-0ca871948ca3` | `collect_logs / sent` | Despachado 09/07, expirado no mesmo dia; evento `job.started`, sem resultado/recibo terminal | Mesmo procedimento auditado de timeout, sem retry |

**Risco apurado:** o pull atual filtra jobs vencidos, e os dois `sent` expirados não devem voltar ao despacho; o `queued` de uninstall será tratado como vencido antes de seleção. Isso **não autoriza** ignorar a inconsistência histórica ou afirmar que os sent concluíram/falharam. Auditoria não foi alterada e não houve polling artificial nem cancelamento. Um `dry-run` de `reconcile_stale_agent_job` já indicou elegibilidade para timeout dos dois antigos.

**Decisão pendente:** operador deve aprovar explicitamente a disposição dos três jobs; Codex deve rever status imediatamente antes de qualquer aplicação, usar somente fluxo auditado existente e registrar antes/depois, motivo e eventual incompatibilidade. Não aplicar cancelamento/timeout como efeito colateral do deploy. Se optar por não reconciliar antes da janela, documentar a aceitação formal do risco residual em vez de dizer que os jobs desapareceram.

## 7. Deploy consolidado — plano, não execução

**Estado: NO-GO operacional, somente preflight e diagnóstico concluídos.** Produção ainda no `8467cd…`, target `866afe…` **não existe como objeto Git no checkout remoto** no último preflight, embora esteja na main do GitHub. Nunca executar `git checkout main` sem SHA congelado.

### Gates antes de iniciar

- [ ] Janela operacional e comunicação de indisponibilidade confirmadas; responsável presente e rollback aprovado.
- [ ] Decisão sobre jobs antigos e revalidação de zero jobs sensíveis ativos, sem executá-los.
- [ ] Verificar novamente SHA, `git status`, units, timer, migrations, flags, espaço, permissões, jobs e usuários de serviço.
- [ ] Criar backup **novo** do checkout atual e árvore `staticfiles/` completa com metadados, owners, `.gz` e checksums; preservar `certs/`/`.env`/`.venv`/mídia/logs.
- [ ] Criar dump PostgreSQL atualizado conforme política aprovada; `pg_dump -Fc` é consistente online, mas a decisão de congelar escritas deve respeitar a janela da empresa. Para diretório `root:root 0700`, usar método comprovado de arquivo temporário root 0600 e stdout de `pg_dump` como postgres; validar tamanho, `pg_restore --list` e SHA antes da renomeação.
- [ ] Não considerar `pg_restore --list` prova de restauração integral. O VZDump protegido da VM 900 (13/11, ~59,75 GB) teve `TASK OK`, mas **não foi restaurado em laboratório**. Backups antigos não substituem checkpoint da janela.
- [ ] Garantir que o alvo está no repositório remoto e que o diff contém exatamente PRs #46/#48/#49; confirmar target imutável.
- [ ] Não mudar flags/allowlists/grants, não iniciar Uvicorn, não gerar release Windows.

### Sequência autorizável após novo GO

1. Coordenar o timer (estado original documentado) e a política de escritas, sem tocar em outros serviços; capturar checkpoint do banco e do código/estáticos.
2. Buscar o commit `866afe…`, verificar, promover esse SHA em checkout detached preservando recursos locais; `manage.py check`, `migrate --plan` **vazio**, sem migration.
3. `collectstatic --dry-run` como `nightowl` e revisar lista; depois `collectstatic --noinput` sem `--clear`, somente se permitido. Não alterar `vendor/xterm` nem permissões recursivamente.
4. Verificar arquivos e `.gz`; iniciar/reiniciar **somente `nightowl.service`** para atualizar views, settings, URLs e templates em cache. Nginx, PostgreSQL e relay devem permanecer em seus estados.
5. Verificar login e rotas HTTP, Dashboard e páginas de endpoint, assets novos por HTTPS `200` com conteúdo validado, e os três cache-busters da PR #47. Esperados: `/static/css/endpoint_services.css?v=services-phase-a-1` e `/static/js/endpoint_services.js?v=services-phase-a-1`.
6. Verificar `NIGHTOWL_SERVICE_COLLECTION_ENABLED=false` e allowlist vazia, API negando consulta e **zero jobs `collect_services` novos**. Verificar `NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS` vazia, `admin_auth=false`, relay inativo, nenhum grant/sessão criado.
7. Restaurar timer ao estado anterior e registrar logs, duração, SHA e hashes. Separar smoke HTTPS de homologação visual autenticada humana.

### Abort/rollback

Se check, static, auth ou serviço falhar, parar a progressão, preservar evidências e retornar **código + árvore estática + metadados** em conjunto a `8467cd9541d5ce93e731a19f1789486abe898402` a partir do backup atual da janela; reiniciar somente `nightowl.service`, revalidar login/assets e timer. **Não** restaurar PostgreSQL automaticamente: este diff não muda schema. Restauração de banco exige evidência de dano e autorização própria.

## 8. Homologação futura — ordem independente

1. **Deploy Linux com flags desligadas** (#46/#48/#49). A primeira homologação é HTTP/UI e negação de acesso.
2. **Terminal Assistido na VM**: janela separada, verificar Tray/processo e usuário interativo, habilitar executor restrito para grant, grant mínimo (p.ex. 30 min) com aprovação, iniciar temporariamente Uvicorn, testar consentimento, I/O, Unicode, Ctrl+C, clipboard, resize, encerramento e revogação; desligar relay ao final.
3. **Serviços Windows**: gerar release inédita assinada oficial (canal development, rollout 0%, pausada), validar trust e updater, atualizar **somente UUID da VM piloto**; confirmar heartbeat `collect_services_paged:2`, habilitar global flag + allowlist somente ao UUID com autorização, ler listagem, paginação/filtros, revogar e revisar jobs. Nenhuma ação iniciar/parar/reiniciar.
4. **WinRM**: teste HTTPS/5986 autorizadamente no Dell físico de domínio; a VM sem domínio permanece ineligível. Não habilitar HTTP/5985 por conveniência.
5. **Outros débitos**: jobs sem resultado/reconciliação, Chrome/CDP intermitente não explicado, testes de restore isolado, segurança de NPM upstream e revisão futura de DLP/SIEM fora deste ciclo.

## 9. Fluxo de trabalho Codex 1 / Codex 2

- **Codex 1:** tem acesso operacional SSH ao NightOwl; responsável por preflight, deploy com janela explícita, rollback, terminal/grants e investigação de jobs. Deve trabalhar com dados reais sanitizados e nunca executar alteração irreversível por inferência.
- **Codex 2:** desenvolvimento Windows/contratos e release; atualmente #46 e #48 integradas; nova release Windows paginada **não foi criada**. Próxima contribuição pode ser preparo do builder oficial após aprovação, sem publicar.
- Pipeline padrão: branch isolada → testes → push → PR → CI → revisão → merge autorizado → preflight → backup → deploy explicitamente autorizado → homologação real. Sempre apresentar HEAD, CI, diff, artefato e condição GO/NO-GO.
- Preservar checkout e arquivos não rastreados dos Codex e `certs/` da produção; nunca usar hostname como identificador de ação.
- O roteiro de documentação da PR #50 (Zabbix/Wazuh) é **projeto independente** e não deve contaminar os commits/deploy deste repositório.

## 10. Checklist de retomada em outro chat

- [ ] Ler este handoff e documentos atuais; **não** usar o handoff de 10/11 como status presente.
- [ ] Verificar no GitHub que main permanece `866afe…`; se avançou, **não aumentar o alvo de deploy automaticamente**.
- [ ] Confirmar por SSH **somente leitura** que produção permanece `8467cd…` e gates são efetivos.
- [ ] Decidir destinação dos três jobs do UUID antigo do CS-CVEL-0254; registrar aprovação separada.
- [ ] Confirmar janela, comunicação, backups e checkpoints; só então considerar GO do deploy.
- [ ] Publicar somente SHA congelado, sem migrations/flags/grants/jobs/Windows.
- [ ] Validar visualmente frontend e negações; depois organizar homologações independentes.
- [ ] Não confundir teste E2E sintético com sessão Windows ConPTY real.
- [ ] Se a próxima conversa for apenas planejamento, não realizar nenhuma alteração de produção.

## 11. Referências primárias

- [RC47 roadmap](deploy-RC47.md); [pré-deploy](rc47-predeploy-homologation.md); [prontidão do backend](rc47-backend-deploy-readiness.md); [ensaio de restore](rc47-database-restore-homologation.md).
- [Serviços Windows, Fase A](rc47-windows-service-manager-phase-a.md); [allowlist](rc47-service-manager-pilot-allowlist.md); [grants ConPTY](rc47-terminal-conpty-grant-operations.md); [WinRM](rc47-09-winrm-actions-jobs.md).
- PRs [#46](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/46), [#47](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/47), [#48](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/48) e [#49](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/49).
- Evidência relatada pelo operador e retorno dos Codex; detalhes de produção não foram reconsultados pela atualização documental em si.

**Regra final:** todo estado neste handoff é uma fotografia datada. Uma operação só existe quando observada no ambiente correto; CI/merge não é deploy, `pg_restore --list` não é restore, heartbeat não prova Tray ativo e `sent` não prova conclusão.
