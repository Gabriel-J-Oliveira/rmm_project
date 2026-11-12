# RC47 — Prontidão para deploy do backend

**Estado:** plano de publicação para revisão; não autoriza execução. Nenhum acesso, alteração ou teste foi feito no servidor.

**Baseline indicada para produção:** `6090228aed49c08ded2ecd8d4ca24658df9eb3b0`
**Main RC47 auditada:** `b8b417f520290db7d0b86af71feb86fccd9bf12b`
**Plano relacionado:** [`rc47-predeploy-homologation.md`](rc47-predeploy-homologation.md)

## Conclusão executiva

O diff contém 64 arquivos (4.344 adições, 540 remoções): 13 arquivos Python de runtime, três migrations aditivas, nove fontes estáticas, cinco templates, 23 arquivos de teste, sete documentos, três workflows e um script de E2E Windows. `requirements.txt` e `pyproject.toml` não mudaram. O escopo é backend/UI; não há necessidade de publicar uma nova versão do agente Windows para este diff.

**NO-GO para iniciar o deploy agora.** O código está pronto para preparação, mas ainda faltam confirmações operacionais: SHA e estado Git efetivos no host no momento da janela, estado das migrations 0048–0050, variáveis carregadas pelos processos reais, validação do caminho WSS/Nginx, `collectstatic` efetivo em staging com as ACLs de produção, backup restaurável recente e tratamento do timer de manutenção. Os dados de produção abaixo foram fornecidos pelo operador; não foram consultados independentemente nesta etapa.

## 1. Baseline e inventário do diff

### Runtime Python e banco

Arquivos de runtime alterados:

- `agents/automation.py`, `agents/management/commands/process_job_automations.py`
- `agents/models.py`, `agents/services.py`, `agents/telemetry_persisted_analysis.py`, `agents/terminal_sessions.py`, `agents/views.py`
- `config/settings.py`, `config/urls.py`
- `dashboard/agent_recovery_views.py`, `dashboard/automation_views.py`, `dashboard/capacity_views.py`, `dashboard/views.py`

Migrations novas na linha-base indicada:

| Migration | Alteração | Compatibilidade / gate |
|---|---|---|
| `0048_separate_admin_terminal_grant` | Adiciona `TerminalAccessGrant.allow_admin_auth=False` e a permissão `open_admin_terminalsession`. | Aditiva. Validar estado aplicado e grants; não habilitar `admin_auth`. |
| `0049_persisted_telemetry_schedule` | Cria agenda por endpoint, com identidade snapshot, próxima execução, retry, revisão e histórico compacto. | Aditiva. Nova tabela pode acumular dados se a flag for habilitada em outra mudança. |
| `0050_telemetry_scheduler_health` | Adiciona contagens, duração, falhas, suspensão e recuperação ao health do scheduler. | Aditiva. Não remover health nem reverter sem avaliar dados gravados. |

A cadeia no código é `0047 → 0048 → 0049 → 0050`. A baseline informada precisa ser confirmada com `showmigrations` e `migrate --plan` no servidor. Este documento não afirma que 0048–0050 estejam pendentes nem autoriza aplicá-las. Não há migration destrutiva no diff; reversão pode eliminar dados novos das tabelas/campos, portanto não presumir rollback de schema seguro.

### APIs e contratos

- `config/urls.py` acrescenta página/API de Inventário de Hardware, consulta e execução de política de automação direcionada ao UUID do endpoint, e consulta de health da análise persistida.
- `dashboard/automation_views.py` e `agents/automation.py` acrescentam execução manual direcionada. A materialização restringe os alvos ao endpoint atribuído, usando UUID, revisão, permissão, request ID e auditoria existentes; não deve transformar uma ação na página do endpoint em execução para a frota.
- O scheduler continua no comando `process_job_automations`, sem novo serviço. A análise de telemetria tem flag independente, limite rígido de cinco por rodada, dry-run por default e primeira captura `unconfigured`.
- `agents/views.py` endurece a revalidação de `update_agent` no pull do agente, conferindo identidade e contrato persistido da release antes de despachar. Isso não muda o protocolo do agente, mas pode cancelar jobs `queued` antigos sem metadados completos. O snapshot de produção informou zero jobs `queued/sent/running` no momento da coleta; repetir a consulta perto da janela e revisar qualquer job que apareça. Não reescrever jobs históricos em lote.
- `agents/terminal_sessions.py` separa autorização administrativa do grant ConPTY assistido, com permissão e `allow_admin_auth` próprios, ambos desligados por default. Isso não autoriza ativar terminal administrativo.
- Não foi observada mudança no protocolo Windows, no coletor, na cadência de telemetria ou no pacote do agente. Compatibilidade RC46 permanece condicionada ao contrato de jobs acima e à configuração/versão do backend.

### Templates, estáticos e demais arquivos

Templates alterados: `templates/base.html`, `templates/dashboard/capacity.html`, `templates/dashboard/endpoint_detail.html`, `templates/dashboard/job_automations.html`, `templates/dashboard/jobs.html`.

Fontes estáticas alteradas:

- CSS: `static/css/endpoint_actions_jobs.css`, `static/css/endpoint_detail_refinement.css`, `static/css/hardware_inventory.css`, `static/css/nightowl.css`.
- JavaScript: `static/js/endpoint_detail.js`, `static/js/endpoint_live.js`, `static/js/endpoint_winrm_recovery.js`, `static/js/hardware_inventory.js`, `static/js/job_automations.js`.

Esses nove arquivos somam 1.125.522 bytes na cópia local do checkout. Todo o diretório `static/` local soma 5.143.936 bytes. São medidas das fontes versionadas locais, não do `STATIC_ROOT`, dos sidecars WhiteNoise nem do espaço transitório necessário em produção.

Outros arquivos do diff: `scripts/Invoke-WindowsE2E.ps1`; workflows `.github/workflows/enrollment-identity.yml`, `.github/workflows/rc47-05-monitoring-ui.yml`, `.github/workflows/rc47-09-winrm-ui.yml`; testes `agents/test_automation.py`, `agents/test_ci_partition.py`, `agents/test_manual_prefix_migration.py`, `agents/test_rollout_dispatch.py`, `agents/test_rollout_preview.py`, `agents/test_telemetry_analysis_scheduler.py`, `agents/test_telemetry_persisted_analysis.py`, `agents/test_telemetry_persisted_analysis_postgresql.py`, `agents/test_terminal_admin_auth.py`, `agents/tests.py`, `dashboard/test_agent_recovery.py`, `dashboard/test_capacity_analysis.cjs`, `dashboard/test_capacity_dashboard.py`, `dashboard/test_execution_policies.py`, `dashboard/test_hardware_inventory.py`, `tests/frontend/test_endpoint_actions_jobs_layout.cjs`, `tests/frontend/test_endpoint_actions_jobs_visual.cjs`, `tests/frontend/test_endpoint_detail_refinement.cjs`, `tests/frontend/test_endpoint_detail_update_modal.js`, `tests/frontend/test_endpoint_live.cjs`, `tests/frontend/test_endpoint_policy_e2e.cjs`, `tests/frontend/test_endpoint_winrm_recovery.cjs`, `tests/frontend/test_job_automations_e2e.cjs`; documentação `docs/rc47-01-hardware-inventory.md`, `docs/rc47-03-incremento-b.md`, `docs/rc47-03-incremento-c.md`, `docs/rc47-04-incremento-a.md`, `docs/rc47-07b-terminal-admin.md`, `docs/rc47-09-winrm-actions-jobs.md`, `docs/rc47-predeploy-homologation.md`.

`requirements.txt` e `pyproject.toml` não têm diferenças entre os SHAs comparados. As mudanças de UI exigem `collectstatic` para o caminho servido pelo Django/WhiteNoise; não exigem Nginx novo por si só. O workflow/scripts Windows são validação e não entram na publicação Linux.

## 2. Serviços, ASGI, Nginx e flags

O snapshot operacional fornecido indica `nightowl.service`, `nightowl-terminal.service` e Nginx ativos; backend e terminal compartilham `/opt/nightowl`; HTTP atual usa Gunicorn/WSGI com três workers; o terminal é um serviço separado. O relay em `agents/terminal_transport.py` mantém sessões em memória por processo. `config/asgi.py` entrega WebSocket ao relay; a garantia exigida é um único processo ASGI para esse relay. Não usar múltiplos workers ASGI nem tratar Gunicorn/WSGI como substituto de ASGI.

Substituir arquivos em disco não atualiza o código carregado nos workers. Se apenas `nightowl.service` for reiniciado, o terminal pode continuar com código antigo; se apenas o terminal for reiniciado, pode continuar falando com endpoints/contratos do backend anterior. Planejar uma janela coordenada: drenar sessões existentes, parar primeiro o terminal ASGI, parar o backend HTTP, atualizar todos os arquivos para o mesmo commit, validar migrations autorizadas e estáticos, então iniciar HTTP e o processo ASGI único com a configuração já aprovada. Reabrir tráfego somente após checks locais e WSS passarem. Confirmar no unit real o comando, usuário, diretório, ambiente, bind e número de workers; não inferir pela flag no Django shell.

Nginx deve continuar encaminhando HTTP e Upgrade WebSocket para os destinos atuais. Não há configuração Nginx versionada alterada nesta diferença. A topologia, headers `Upgrade`/`Connection`, timeout de leitura e TLS efetivos não foram inspecionados aqui; validar configuração sem publicar `nginx -T` bruto, testar `nginx -t` e verificar conexão WSS autenticada em homologação controlada. A última ocorrência Chrome/CDP continua sem causa comprovada; não confundir esse risco histórico do runner com evidência de falha WSS em produção.

### Configuração

Valores fornecidos pelo operador via Django shell não comprovam o ambiente efetivamente carregado por cada processo. Defaults de código observados em `config/settings.py`:

| Flag/política | Default de código | Observação |
|---|---:|---|
| `NIGHTOWL_JOB_AUTOMATIONS_ENABLED` | false | Não ativar como parte do deploy. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED` | false | Manter desligada. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED` | false | Manter desligada; nenhuma análise automática. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN` | true | Manter. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY` | `unconfigured` | Manter sem decisão operacional. |
| `NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED`, `...GOVERNANCE_ENABLED`, `NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED` | false | Não ativar. |
| `NIGHTOWL_TERMINAL_ENABLED`, `...SINGLE_PROCESS`, `...CONPTY_ENABLED`, `...BROWSER_ENABLED` | false | Preservar configuração atual explicitamente aprovada; não ampliar durante deploy. |
| `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED` | false | Manter desligada e não homologada. |
| `NIGHTOWL_ADMIN_ACTIONS_ENABLED`, `NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED`, `NIGHTOWL_APPROVED_SCRIPTS_ENABLED` | false | Os valores de shell informados divergem dos defaults em alguns itens; não os substituir. Confirmar valores efetivos de cada processo, sem imprimir variáveis completas ou segredos. |

O snapshot reporta ainda `TERMINAL_ENABLED=True`, `TERMINAL_SINGLE_PROCESS=True`, `TERMINAL_CONPTY_ENABLED=True`, `TERMINAL_BROWSER_ENABLED=True`, ações operacionais/scripts aprovados `True`, terminal admin `False`, jobs e rollout `False`. Preservar a configuração operacional atual: mudança de código ou restart não é autorização para editar flags. Obter evidência sanitizada por processo e comparar WSGI e ASGI antes/depois.

O `nightowl-maintenance.timer` é informado como ativo a cada cinco minutos, executando como `root`; cinco execuções recentes tiveram 25 tarefas aprovadas e zero falhas. O timer não está versionado no diff. Antes da janela, identificar tasks concretas e possíveis escritas concorrentes com migrations/arquivos; coordenar eventual pausa somente com autorização operacional separada. Revisar privilégio mínimo de root em outro trabalho; não mudar agora.

## 3. `collectstatic`, WhiteNoise e permissões

`config/settings.py` define `STATIC_ROOT = BASE_DIR / 'staticfiles'` e `CompressedStaticFilesStorage` por default. O servidor usa `.gz`/`.br`; a presença de sidecars e sucesso de escrita reportados precisam ser repetidos no staging com a mesma versão instalada do WhiteNoise, backend de storage e ACLs. `collectstatic --dry-run` não prova escrita, recompressão nem substituição.

O snapshot informa `staticfiles` pertencente a `nightowl`, sidecars existentes verificados e `staticfiles/vendor`/`vendor/xterm` pertencentes a root, modo 755, sem escrita para `nightowl`. As fontes `static/vendor/xterm` existem no repositório, mas não foram alteradas no diff. Ainda assim, o pós-processamento do storage pode revisitar os arquivos coletados. Não fazer `chown -R`, `chmod -R`, `--clear` ou correção preventiva.

Antes do deploy, usar uma cópia isolada do `STATIC_ROOT` real, preservando owners/modos/ACLs e espaço de disco, e executar `collectstatic` efetivo como `nightowl` com a configuração/versões exatas. Capturar lista de arquivos gravados, sobrescritos e removidos, erros de permissão, hashes, tamanho dos assets e sidecars; verificar que a fonte e os arquivos existentes permanecem restauráveis. Validar os arquivos servidos por Nginx e referências de cache busting. Se falhar, determinar primeiro se WhiteNoise tenta alterar `vendor/xterm`; então corrigir somente o diretório/arquivos exatos que o teste provar necessários, após aprovação. Alternativa é produzir o conjunto final em staging gravável e promover de forma controlada, mas só se compatível com o caminho servido e rollback.

Na última coleta havia 3,1 GB livres na raiz e 139 GB no volume PostgreSQL/backups. Isso não substitui medição imediatamente anterior. Os 1,13 MB de fontes estáticas RC47 não representam o pico de espaço: medir cópia integral/staging, compressão, logs e backup; abortar se o espaço não cobrir o pior caso previsto com reserva acordada.

## 4. Plano de publicação — execução somente após autorização separada

### Checkpoint 0 — congelar o alvo

- Registrar SHA alvo exato `b8b417f520290db7d0b86af71feb86fccd9bf12b` e SHA real de `/opt/nightowl`; confirmar `git status`, branch/detached HEAD e diff. O `certs/` não rastreado é preservado: não usar `git clean`, reset destrutivo, cópia recursiva sobre ele, nem abrir/exportar seu conteúdo.
- Confirmar que não há commits posteriores incorporados ao artefato; comparar o inventário completo do diff e confirmar ausência de arquivo inesperado.
- Coletar `showmigrations agents`, `migrate --plan`, `check`, versão/runtime instalado, serviço/usuário/diretório de trabalho de ambos processos, invocadores/timer, estado de Nginx e jobs de update pendentes. Não mostrar ambientes, tokens ou chaves.
- Revisar uma lista explícita de flags permitidas e valores por processo; nenhuma alteração de flag neste plano.

### Checkpoint 1 — backup e retorno

- Produzir backup consistente e recente do PostgreSQL e dos arquivos/configuração que o procedimento substituirá; preservar o backup de 11/11 e Veeam como cópias anteriores, não como único ponto de retorno.
- Registrar checksum, permissões, tamanho, destino protegido e tempo de restauração. Demonstrar restore em banco isolado representativo; verificar schema e consultas antes de aprovar a janela.
- Capturar snapshot de versões/owners/modos/ACLs de `staticfiles`, arquivos da aplicação e units sem copiar segredos para logs.
- Definir quem decide rollback e como lidar com escritas posteriores ao backup. Não presumir rollback de banco por downgrade de migration.

### Checkpoint 2 — validar artefato e dados

- Preparar código imutável do SHA aprovado fora do checkout operacional atual; não sobrescrever nem limpar `/opt/nightowl/certs`. Usar o mecanismo de publicação aprovado pelo Codex 1, mantendo venv/dependências se os manifests e instalação comparada forem iguais.
- Executar verificações locais do SHA: `python manage.py check`, `python manage.py makemigrations --check --dry-run`, testes CI aprovados e `migrate --plan` usando configuração isolada/produção clonada, sem gravar no banco real.
- Confirmar disponibilidade e compatibilidade de Django 6/DRF/Gunicorn/Uvicorn/WhiteNoise já instalados. Não há dependências declaradas novas, mas confirmar versões realmente carregadas.
- Revisar jobs `update_agent` atuais imediatamente antes da janela. Se existir `queued`, validar contrato de release individual e identidade; `sent/running` deve ser tratado como estado em curso/incerto. Não fazer replay ou cancelamento em massa.
- Executar o teste efetivo de `collectstatic` no staging com usuário, storage e ACLs reais replicados; comparar arquivos/sidecars e verificar leitura HTTP. Falha de permissão ou saída diferente é NO-GO.

### Checkpoint 3 — janela e corte coordenado

1. Confirmar backup testado, alvo, plano, responsáveis, janela e critério de abortar.
2. Verificar que não há operação de endpoint, rollout ou ação WinRM em andamento que dependa do backend; não executar essas operações neste plano.
3. Drenar sessões de terminal assistido de modo comunicado. Não iniciar `admin_auth` nem novas sessões durante o corte.
4. Coordenar o timer de manutenção de 5 min com o responsável. Garantir que não execute tarefas concorrentes à mudança de código/schema/assets; não mudar o timer sem autorização.
5. Parar `nightowl-terminal.service` para encerrar o HUB process-local graciosamente; confirmar sessões encerradas. Parar `nightowl.service`/Gunicorn para evitar workers antigos servindo durante a troca. Nginx pode permanecer atendendo uma resposta de manutenção/indisponibilidade planejada; não alterar a configuração.
6. Ativar o checkout/código imutável RC47 e assets staged no caminho aprovado, preservando `certs/`, venv, mídia, logs e static files fora do escopo. Confirmar hashes de arquivos implantados.
7. Somente após decisão explícita do responsável de banco: aplicar exatamente as migrations aprovadas, na ordem retornada pelo plano conferido. Não usar `migrate` genérico sem revisar o plano. Guardar saída e duração.
8. Iniciar `nightowl.service` (HTTP/WSGI) e confirmar health/autenticação/DB; depois iniciar `nightowl-terminal.service` como exatamente um processo ASGI, com o mesmo SHA e ambiente aprovado. Não iniciar com múltiplos workers.
9. Reabrir tráfego após todos os smoke checks. Manter timer, flags e rollout iguais ao snapshot aprovado.

Se o procedimento real usar uma ordem/cutover diferente, documentar e testar em staging antes; não improvisar atualização in-place durante a janela.

### Checkpoint 4 — smoke checks e observabilidade

- `systemctl is-active nightowl.service nightowl-terminal.service nginx`; conferir `MainPID`, reinícios, `User`, `Group`, `WorkingDirectory`, executável e número de processos sem imprimir ambiente completo.
- Verificar estado de schema e migration (`showmigrations`, `migrate --plan`), `manage.py check` em contexto seguro, conexão ao PostgreSQL e rota de login. Não há endpoint genérico `/health` identificado como contrato no diff; usar endpoint real existente após confirmar rota e autenticação, não inventar path.
- Como operador autenticado autorizado, abrir páginas de endpoint, Monitoramento e Políticas; confirmar assets CSS/JS servidos com status 200 e sem erros de console. Validar um GET de leitura e CSRF/autorização normal; não despachar job neste smoke.
- Validar WSS pelo caminho público, com operador/grant de laboratório autorizado e endpoint de laboratório; confirmar sessão/tickets, identidade, consentimento e encerramento. Não usar endpoint operacional real nem terminal administrativo como smoke. Se não houver alvo de laboratório, WSS permanece não homologado e gate do terminal é NO-GO.
- Confirmar os campos de flags pelos processos HTTP e ASGI sem imprimir env, scheduler ainda desligado, dry-run ligado, first capture `unconfigured`, admin auth e rollout desligados.
- Observar logs sanitizados do serviço e Nginx, erros 4xx/5xx, falhas de asset, reconnect e health por pelo menos o período acordado. Não guardar corpo de terminal, senhas ou tokens.

## 5. Rollback e critérios objetivos

**Abortar antes da troca** se: SHA/diff divergir; `certs/` ou checkout local estiver em risco; migrations aplicadas/plano diferirem; backup não tiver restore demonstrado; espaço abaixo da reserva; ACL de arquivo/sidecar falhar; dependências runtime não coincidirem; jobs `update_agent` estiverem sem classificação; flags por processo divergirem ou forem inesperadas; timer concorrente não estiver entendido; Nginx/WSGI/ASGI não puderem ser coordenados.

**Abortar após iniciar** se: migration falhar parcialmente; HTTP não autenticar/conectar DB; ASGI não estiver em um processo ou não aceitar WSS autenticado; erros 5xx, assets 404/403, integridade de arquivos divergente, incompatibilidade de schema ou falha de sessão/autorização. Capturar diagnóstico sanitizado e interromper novas operações; não tentar corrigir permissões ou flags ad hoc.

**Rollback de aplicação:** restaurar o checkout anterior imutável e os assets anteriores/checksum a partir do checkpoint; iniciar HTTP e depois ASGI no mesmo SHA antigo; verificar saúde, login, assets e comportamento. Preferir manter migrations aditivas aplicadas se o código anterior as tolerar. Para `0048–0050`, verificar essa compatibilidade em clone antes. Não executar migration reversa automaticamente: pode apagar coluna/tabela, grants, agenda ou health. Restaurar banco inteiro exige decisão sobre todas as escritas após o backup, parada/coordenacão do tráfego e restore validado; não misturar código antigo com banco restaurado a ponto temporal diferente sem análise.

## 6. Matriz de riscos

| Risco | Nível | Evidência / incerteza | Controle antes do GO |
|---|---|---|---|
| Migrations 0048–0050 e rollback | Alto | Três migrations aditivas na cadeia; estado aplicado efetivo não veio nesta tarefa. | `showmigrations`, `migrate --plan`, restore testado e aprovação Codex 1; sem reverse automático. |
| Workers em código misto | Alto | Gunicorn WSGI e terminal ASGI são unidades separadas; relay possui estado process-local. | Corte coordenado e mesmo SHA em ambos; um processo ASGI. |
| Nginx/WSS real | Alto | Nginx está ativo segundo snapshot; configuração e handshake não foram coletados. | Sanitizar/validar proxy e executar WSS autenticado em homologação. |
| `collectstatic`/WhiteNoise | Alto | Storage comprimido; árvore `vendor/xterm` sem escrita por nightowl. Não provado que post-process falhe. | `collectstatic` efetivo em staging com ACLs equivalentes; sem chown/chmod preventivo. |
| Jobs update antigos | Médio-alto | Revalidação no pull pode cancelar contrato legado inválido; zero jobs no snapshot não garante zero agora. | Recontar e inspecionar payload sem segredos imediatamente antes. |
| Configuração por processo | Alto | Valores conhecidos são de shell e podem divergir do processo systemd. | Verificar allowlist de flags em HTTP e ASGI; preservar valores, não ativar flags. |
| Timer de manutenção root | Médio-alto | Execução a cada cinco minutos informada como root; task e concorrência na janela não auditadas. | Inventariar tasks e coordenar janela; revisar privilégio mínimo separadamente. |
| Espaço local e backup | Médio-alto | 3,1 GB raiz/139 GB volume de backup no último snapshot; backup de 11/11 não teve restore provado aqui. | Medir de novo, backup recente, checksum e restauração isolada. |
| Compatibilidade Windows RC46 | Médio | Agente/protocolo não mudam; backend endureceu despacho de updates e add APIs. | Verificar fila, manter contratos v1/v2; sem release ou atualização Windows. |

## 7. Evidência de validação nesta preparação

- Baseline `6090228...` é ancestral de `main`; comparação executada localmente contra `b8b417f...`.
- Diff: 64 arquivos, +4.344 / −540; sem alterações em `requirements.txt` ou `pyproject.toml`.
- Migrations observadas: 0048, 0049 e 0050, dependência linear após 0047.
- Fontes estáticas RC47 no checkout: 9 arquivos, 1.125.522 bytes; `staticfiles/` não existe na cópia local, portanto não houve prova de `collectstatic` efetivo nem teste de ACL equivalente.
- CI da main decorre da validação aprovada da PR #42 no HEAD pai `165dd1c...`: regressões SQLite, PostgreSQL, deployment Windows, Windows .NET/terminal E2E, browser comparison e gates visuais RC47-05/09 passaram. Isso valida o código testado no CI, não o host, banco, ACLs ou comportamento real do deploy.
- Nesta PR documental: executar `git diff --check`; não executar suíte de código, comandos Django com configuração potencialmente operacional, `collectstatic`, migrations ou qualquer teste contra produção.

## 8. GO / NO-GO e decisões humanas pendentes

**GO para a revisão deste plano documental. NO-GO para deploy neste momento.** Reavaliar somente quando todos os itens abaixo forem registrados pelo operador/Codex 1:

1. SHA real do backend e schema aplicados no host, diff final e artefato alvo confirmados.
2. Restore recente de PostgreSQL demonstrado; ordem e autorização de 0048–0050 aprovadas pelo responsável.
3. Units reais (processo/usuário/diretório/ambiente/bind/workers) e configuração Nginx/WSS validadas sem exposição de segredos.
4. Estado de flags confirmado diretamente nos dois processos; sem mudança em flags nesta publicação.
5. Fila de update recontada/revisada; timer root e janela de cinco minutos coordenados.
6. `collectstatic` efetivo passou em staging com armazenamento comprimido, ACLs e sidecars equivalentes; vendor/xterm não requer correção preventiva.
7. Espaço, artefato, checksums, smoke, duração e rollback validados em staging.
8. Smoke autenticado, inclusive WSS, executado apenas em endpoint de laboratório elegível. Nenhuma ação real em endpoint corporativo.

Ficam fora deste plano: qualquer mudança de flag, habilitação do scheduler, `admin_auth`, rollout, alteração em GPO/WinRM, publicação/atualização do agente Windows, operação de jobs ou execução de deploy. A RC47-09 não é nova autorização para ampliar terminal; a ocorrência intermitente de inicialização Chrome/CDP continua registrada como **sem causa confirmada**.
