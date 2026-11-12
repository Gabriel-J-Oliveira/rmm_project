# RC47 — Auditoria de pré-deploy e plano de homologação

**Estado:** preparação para revisão; não autoriza deploy, migrations, mudança de flags, publicação Windows ou operação remota.

**Main auditada:** `0e85825de2bf4910ab6f21055e3583348734ea3c`.

## 1. Conclusão e limites da evidência

A main contém as migrations `agents.0047` a `agents.0050`, com dependência linear. O repositório define defaults seguros para análises persistidas, rollout automático e terminal administrativo. O comando versionado de scheduler é `process_job_automations`; não há timer/cron/serviço periódico correspondente versionado neste checkout.

**Referências de versão devem ser distinguidas.** A última baseline de produção informada foi `46d3b5f6dc6376bc96d1e7305cf250bb9a0cc7a3`, em 10/11/2026; esta informação não confirma o SHA que está efetivamente implantado agora. O commit `24f84c9...` é uma referência histórica anterior e não deve ser usado como baseline atual presumida. A baseline de integração RC47 auditada aqui é `0e85825de2bf4910ab6f21055e3583348734ea3c`, não evidência de produção. **O SHA efetivamente implantado permanece não verificado até coleta direta no servidor.** Antes de calcular migrations pendentes ou preparar uma janela, o operador deve coletar no host o SHA real, estado Git, migrations aplicadas e invocadores externos. Até isso ocorrer, `0047–0050` são migrations candidatas à avaliação, não uma afirmação de que todas estejam pendentes.

Não há acesso ou evidência aqui sobre valores atuais de `.env`/systemd, serviços ativos, dados de produção, jobs pendentes, backups restauráveis ou timers instalados. Esses itens permanecem gates operacionais.

A RC47-09 permanece em desenvolvimento/revisão separada na PR #42 (`codex/rc47-09-winrm-actions-jobs`), que estava aberta na consulta desta revisão. Este documento descreve somente a main `0e85825`; não representa nem homologa o conteúdo ainda não integrado da RC47-09. Após a integração, revisar novamente diff, migrations, flags, APIs, jobs e roteiro operacional antes de tratar o plano como final.

## 2. Checklist de pré-deploy

### Antes de qualquer janela

- [ ] Confirmar o SHA de produção em `/opt/nightowl`, `git status`, branch/tag e artefato efetivamente servido.
- [ ] Confirmar que o alvo é a main aprovada e registrar seu SHA imutável. Não atualizar implicitamente para uma main posterior.
- [ ] Comparar `git diff --name-status <SHA-producao>..<SHA-alvo>`; classificar alterações de Python, templates, CSS/JS, dependências, configuração e migrations.
- [ ] Capturar `showmigrations` e `migrate --plan`; identificar exatamente o que está aplicado e o que seria executado.
- [ ] Verificar jobs `update_agent` em `queued`, `sent` e `running`, campanhas ativas e jobs com resultado incerto antes de publicar o gate de despacho.
- [ ] Obter backup consistente do PostgreSQL e dos arquivos/configuração que serão substituídos; registrar hashes, permissões e caminho protegido.
- [ ] Demonstrar restauração do backup em ambiente isolado e validar aplicação/schema restaurados. Um arquivo de backup existente não prova que ele restaura.
- [ ] Confirmar espaço em disco, ownership de `staticfiles`, ACLs e escrita de `nightowl` nos destinos/sidecars que `collectstatic` pode reprocessar.
- [ ] Revisar flags selecionadas sem imprimir segredos; registrar somente booleanos/políticas relevantes e manter defaults até decisão separada.
- [ ] Confirmar serviços HTTP/ASGI, proxy, Nginx e invocadores periódicos reais, sem deduzir seu estado pelo código.
- [ ] Definir responsáveis, janela, smoke checks, critérios de abortar e plano de rollback. Não misturar release de backend com atualização do agente Windows.

### Coleta somente leitura no host

Executar por acesso administrativo autorizado, adaptando o caminho do virtualenv ao serviço instalado:

```bash
cd /opt/nightowl
git rev-parse HEAD
git status --short --branch
git log -1 --format='%H %cI %s'
python manage.py showmigrations agents
python manage.py migrate --plan
python manage.py check
python manage.py makemigrations --check --dry-run
systemctl status nightowl.service nightowl-terminal.service nginx --no-pager
systemctl list-timers --all
```

Verificar cron e timers do usuário de serviço conforme a configuração local. Não publicar o conteúdo integral de `systemctl show Environment`, `.env`, units ou logs: podem conter segredos. Para consultar flags efetivas, executar `manage.py shell` no mesmo ambiente Python/configuração do serviço e imprimir somente uma allowlist de nomes de flags, nunca o ambiente inteiro. O estado de flags acima de tudo deve ser confirmado contra o processo que serve a aplicação, não apenas contra um arquivo.

Para comparar arquivos sem alterar o checkout:

```bash
git diff --name-status <SHA-producao-confirmado>..<SHA-alvo>
git diff --stat <SHA-producao-confirmado>..<SHA-alvo>
```

## 3. Migrations e reversibilidade

| Ordem | Migration | Conteúdo observado no código | Risco/checagem |
|---|---|---|---|
| 1 | `agents.0047_persistedtelemetryanalysis_and_more` | Cria `PersistedTelemetryAnalysis` e recibos `TelemetryAnalysisIncorporation`, com índices/constraints de idempotência. | Confirmar ausência de divergência de schema e backup. Rollback da migration pode remover análises/recibos existentes. |
| 2 | `agents.0048_separate_admin_terminal_grant` | Adiciona `TerminalAccessGrant.allow_admin_auth` com default `False` e permissão `open_admin_terminalsession`. | Confirmar grants existentes continuam sem autorização administrativa implícita. Não habilitar `admin_auth`. |
| 3 | `agents.0049_persisted_telemetry_schedule` | Cria agenda por endpoint, retry/backoff e estado de revisão. | Tabela nova pode receber estado quando scheduler for habilitado. Reverter depois de uso pode remover agenda e histórico operacional. |
| 4 | `agents.0050_telemetry_scheduler_health` | Amplia health do scheduler: contagens, falhas, duração, suspensão e recuperação. | Não apagar estado de health; confirmar integridade após migrate e saúde do scheduler. |

Dependências verificadas: `0047 → 0048 → 0049 → 0050`; no escopo `0047–0050` não há migration de outra app identificada. A ordem acima é a ordem do grafo, mas somente `migrate --plan` no SHA/configuração de produção determina o que efetivamente falta.

**Rollback:** preferir rollback de código compatível mantendo migrations aditivas no banco quando o código anterior as ignora corretamente, após teste em cópia. Não executar `migrate agents 0046` automaticamente: as migrations reversas podem apagar tabelas/campos e dados novos. Restaurar dump do banco só com decisão explícita sobre perda de escritas posteriores ao backup, pausa de tráfego/jobs e validação da consistência código/schema. Restaurar arquivos e banco de pontos incompatíveis não é rollback válido.

Pós-migration (se autorizada em etapa posterior): repetir `showmigrations`, `migrate --plan`, `check`; confirmar tabelas/colunas esperadas por introspecção read-only; validar logs e conexão; verificar a rota de saúde do scheduler sem ativá-lo. Nenhum comando de migration foi executado nesta auditoria.

## 4. Matriz de recursos e flags

Os valores abaixo são **defaults no código**, não valores de produção.

| Recurso | Configuração/default | Efeito e gate adicional |
|---|---|---|
| Automações de jobs | `NIGHTOWL_JOB_AUTOMATIONS_ENABLED=False` | `process_job_automations` não processa automações de jobs sem habilitação explícita. Flag não cancela jobs já enviados. |
| Análise persistida manual | `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED=False` | Bloqueia gravação manual; preview continua separado. |
| Scheduler de análise | `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=False` | Desliga a análise periódica, independentemente da flag de jobs. |
| Dry-run de análise | `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=True` | Mesmo habilitando o scheduler, não aplicar sem também decidir explicitamente `False`. |
| Primeira captura | `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY='unconfigured'` | Impede primeira captura automática até política ser decidida. Opções documentadas incluem `immediate` e `staggered_24h`; não escolher sem aprovação. |
| Orquestrador/governança/rollout automático | `NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False`, `NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED=False`, `NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False` | Preview não equivale a despacho. Release, assinatura, elegibilidade, endpoint, grupos, janela e rollout continuam gates separados. |
| Terminal | `NIGHTOWL_TERMINAL_ENABLED=False`, `NIGHTOWL_TERMINAL_SINGLE_PROCESS=False`, `NIGHTOWL_TERMINAL_CONPTY_ENABLED=False`, `NIGHTOWL_TERMINAL_BROWSER_ENABLED=False` | Todos são independentes e necessários conforme o fluxo. Grants `allow_conpty` e `allow_admin_auth` têm default `False`. |
| Terminal administrativo | `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=False` | Não homologado para distribuição; não ativar neste plano. Nunca inferir autorização a partir de `allow_conpty`. |
| Ações administrativas | `NIGHTOWL_ADMIN_ACTIONS_ENABLED=False`; scopes default `{}` | Operações permanecem bloqueadas sem flag, escopo, permissão e autorização tipada. |
| Scripts aprovados | `NIGHTOWL_APPROVED_SCRIPTS_ENABLED=False` | Catálogo não implica execução habilitada. |
| Ações operacionais | `NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=False` | Sem execução por default; escopos são independentes. |
| Agente por endpoint | `auto_update_enabled=False`, política `manual`; canal default `stable`; pausa de endpoint default `False` | Valores persistidos por endpoint. Não alterá-los como efeito colateral do deploy. Campanhas e release têm estado/rollout próprios. |

Não habilitar nenhuma flag como parte deste documento. O valor real, proveniência (unit/env/arquivo) e processo que o consome precisam ser confirmados em produção com saída sanitizada.

## 5. Compatibilidade com o agente RC46 e jobs preexistentes

O gate RC47-08 é principalmente server-side. O protocolo de pull continua aceitando os contratos existentes Jobs v1/v2 e a resposta usa o tipo `update_agent` e payload do updater já existente; não foi identificada nova exigência de protocolo Windows nesta mudança. Isso não garante que qualquer versão de agente/updater possa instalar qualquer release: `minimum_updater_version`, bootstrap, canal do artefato, assinatura/trust, hash, versão e rollback continuam determinantes por endpoint.

Na criação, a elegibilidade é reavaliada novamente no pull. Jobs `update_agent` antigos que não contenham identidade/contrato completo da release (por exemplo `source`, `release_id`, versão, canal, URL, hash, tamanho e estado de assinatura) podem falhar fechados, ser cancelados com `UPDATE_DISPATCH_REVALIDATION_FAILED` e não aparecer na resposta ao agente. Antes de publicar, inventariar e classificar jobs `queued`; não reescrever payloads históricos em massa. `sent`/`running` já foram despachados e não são revogados retroativamente: reconciliar estados incertos antes de qualquer retry.

Trocar canal e despachar update são operações distintas. A API RC47-08 rejeita mudança de canal enquanto houver atualização pendente/em execução; após a integração, política por endpoint da RC47-04B deve continuar restrita ao endpoint escolhido. Alterar canal não cria job automaticamente.

## 6. Dependências, serviços, scheduler e estáticos

- `requirements.txt` declara Django 6.0, DRF 3.17, Gunicorn 23, Uvicorn 0.38, `websockets` 15, WhiteNoise 6.9, psycopg 3.2, entre outras dependências com faixas fixadas. Isso descreve o checkout, não o ambiente instalado. Comparação contra produção depende do SHA/lock de produção, não disponível nesta auditoria.
- Existem `config.wsgi` para HTTP e `config.asgi` para HTTP/WebSocket. A documentação RC46 requer um único processo ASGI para o relay terminal, sem compartilhar sessões entre workers. Verificar units reais; o repositório não fornece evidência de quais processos estão ativos no host.
- O código versiona o management command `process_job_automations`, mas não contém cron/systemd timer/Celery beat que prove execução recorrente. A frequência e flags reais no servidor precisam ser consultadas. A feature de análise tem health próprio; scheduler jobs e análise usam flags independentes.
- As mudanças RC47 incluem templates e assets estáticos (Inventário de Hardware, Monitoramento, endpoint/terminal e Políticas). `collectstatic` e a compressão WhiteNoise podem substituir sidecars `.gz`/`.br`. Validar escrita efetiva de `nightowl`, owners/ACLs, backup de fontes e sidecars e Nginx antes da publicação; `collectstatic --dry-run` não comprova que será possível sobrescrever sidecars.
- Não foi possível afirmar quais desses arquivos já estão publicados, pois falta o SHA real do host. No preflight, listar os caminhos exatos pelo diff entre SHA de produção e alvo; não copiar diretórios inteiros nem normalizar permissões em massa.

## 7. Roteiro de homologação funcional

Executar em ambiente autorizado e preferencialmente primeiro em staging/piloto; esta seção é checklist futuro, não ação realizada.

### Inventário de Hardware

- Abrir `/hardware-inventory/`; conferir totais, paginação/filtros e detalhe por endpoint.
- Comparar fabricante/modelo, SO, CPU, RAM, módulos/slots, disco/volumes, bateria e timestamps com o último snapshot real.
- Ausências devem continuar “sem evidência”; nenhum campo deve ser convertido silenciosamente em zero.
- Confirmar compatibilidade das rotas antigas `/capacity/` e `/api/capacity/...` e que alertas/telemetria antigos continuam preservados.

### Monitoramento

- Em endpoint de laboratório autorizado, alternar interfaces de rede e discos, incluindo nomes longos, valores ausentes e atualização/re-renderização.
- Conferir séries, unidades, estado vazio, tema claro/escuro, teclado/foco e tamanhos desktop/tablet/mobile.
- Confirmar que cards informativos não têm hover de elevação e controles interativos mantêm foco/hover.
- Verificar cache/telemetria recente e origem/timestamps; não usar fixtures sintéticas como prova operacional.

### Políticas de Execução

- Abrir `/jobs/?tab=automations`; confirmar listagem, filtros, próxima ocorrência e histórico.
- Na página de endpoint, validar as políticas atribuídas e que executar manualmente exige permissão/confirmação.
- Testar alteração apenas em alvo descartável: selecionar um endpoint, confirmar UUID/identidade, revisar preview, CSRF, auditoria e limite de execução ao alvo selecionado.
- Salvar/alterar política não deve criar execução automática. Scheduler segue desligado salvo aprovação separada.

### Terminal e WinRM

- Terminal ConPTY assistido: requer flags independentes, permissão, grant `allow_conpty` válido, capability reportada pelo agente e consentimento local do Tray. Verificar criação, entrada/saída, rolagem, Ctrl+C, expiração, revogação e ausência de persistência de conteúdo.
- `admin_auth`: permanece NO-GO para habilitação/distribuição ampla; flag e grant devem continuar falsos. Não usar a sessão assistida como prova de modo administrativo.
- WinRM: apenas diagnóstico read-only em alvo autorizado; registrar DNS, TCP, TLS/autenticação e identidade. Não modificar listener, firewall, certificado ou GPO neste plano.

### Atualização do agente

- Não há atualização em massa autorizada por esta homologação.
- Para eventual piloto posterior: confirmar endpoint por UUID e `machine_id`, estado/heartbeat, versão Agent/Updater, canal e jobs conflitantes; validar SHA/signatura/trust/manifesto da release e rollback; solicitar aprovação específica do endpoint; despachar manualmente um único alvo e confirmar serviço, versão, heartbeat e reconciliação.
- Antes de qualquer disparo, examinar jobs `update_agent` preexistentes e motivo de bloqueio. Rollout automático continua desabilitado por default e percentual de rollout não substitui pausa/assinatura/elegibilidade.

## 8. Piloto Windows e critérios GO/NO-GO

Os gates abaixo são independentes: GO para uma etapa não autoriza as seguintes.

### Gate de migrations

- **GO:** SHA de produção e schema conhecidos; `migrate --plan` revisado no ambiente/DB corretos; migrations testadas em PostgreSQL isolado com volume representativo; backup restaurado; janela, duração/locks e validação pós-migration aprovados.
- **NO-GO:** estado de produção desconhecido; migrations parcialmente aplicadas sem explicação; plano contém migration inesperada; restore não demonstrado; risco de lock/downtime não avaliado; ou rollback depende de apagar dados sem aprovação.

### Gate de deploy backend/estáticos

- **GO:** diff restrito e revisado; dependências e serviços efetivos compatíveis; jobs pendentes classificados; backup de banco/arquivos restaurável; usuário de serviço consegue escrever em todos os alvos gerados pelo `collectstatic`; smoke checks, logs e rollback preparados.
- **NO-GO:** SHA/diff não aprovado; divergência local inesperada; sidecar/ACL bloqueando escrita; falha em check/migration plan/collectstatic; falta de backup testado; flags efetivas inesperadas; health ou logs degradados; ou artefato servido diferente do commit autorizado.

### Gate de homologação Windows

- **GO:** backend aprovado separadamente; endpoint de laboratório/piloto identificado por UUID + `machine_id`; agente/updater e release assinada compatíveis; autorização específica por alvo; jobs incertos ausentes ou reconciliados; plano de observação e rollback aprovado.
- **NO-GO:** identidade ambígua; release sem assinatura/trust/manifesto/hash válidos; updater incompatível; job anterior incerto/conflictante; execução implicar habilitar `admin_auth`, rollout automático ou permissão não homologada; ou ausência de consentimento/rollback.

**Este relatório não satisfaz sozinho nenhum gate operacional**: os resultados dependentes do servidor, de backup restaurado e de Windows real precisam ser anexados como evidência antes da aprovação.

Piloto recomendado em duas fases distintas: (1) publicar backend e verificar migrations/HTTP/estáticos sem jobs ou atualização de agente; (2) somente com autorização operacional separada, testar funcionalidades específicas em VM descartável e depois um único endpoint Windows autorizado. Não executar ações remotas neste plano.

## 9. Rollback controlado

1. Interromper avanço e registrar timestamp, SHA e sintomas. Não iniciar correção improvisada.
2. Se problema de aplicação/estáticos e schema for compatível, restaurar checkout/arquivos/sidecars do backup mantendo migrations aditivas; reiniciar apenas os serviços que o plano da publicação autorizou.
3. Se houver incompatibilidade de schema, interromper tráfego/jobs conforme procedimento aprovado e restaurar código + banco do mesmo ponto consistente. Avaliar explicitamente escritas ocorridas depois do backup.
4. Não reverter migrations `0047–0050` às cegas: `0047` e `0049` contêm dados operacionais potencialmente novos; `0050` guarda health; `0048` muda autorização/grants. Reversão de schema exige plano validado e aprovação específica.
5. Desabilitar timer/flag somente com avaliação de ocorrências/jobs já emitidos; desligar flag não cancela jobs `sent`/`running`. Reconciliar jobs incertos antes de retry.
6. Validar versão, `/health` aplicável, login, endpoints, logs, arquivos estáticos, migrations e integridade do backup restaurado. Preservar cópias da tentativa para análise.

## 10. Evidências operacionais a coletar

Preencher esta lista com evidências datadas, indicando comando/fonte, host/serviço e responsável. Sanitizar secrets, tokens, DSNs, chaves e payloads.

- [ ] **Versão implantada:** SHA completo do checkout em `/opt/nightowl`, estado limpo/sujo, branch/tag e verificação por artefato efetivamente servido. Anexar o SHA alvo aprovado e o diff.
- [ ] **Migrations aplicadas:** saída de `showmigrations` e `migrate --plan`, registrada contra a conexão/schema corretos. Marcar explicitamente quais de `0047`, `0048`, `0049`, `0050` estão aplicadas/pendentes; não deduzir pelo código.
- [ ] **Flags:** valores efetivos apenas da allowlist descrita na seção 4, origem/configuração e processo que as consome. Não anexar `.env`, `systemctl show Environment` integral nem segredo.
- [ ] **Serviços WSGI/ASGI:** units/processos ativos, comando sem credenciais, usuário, versão Python/pacotes e número de workers/processos ASGI. Confirmar separadamente HTTP, WebSocket e proxy.
- [ ] **Timers/agendamentos:** systemd timers, cron e quaisquer invocadores externos de `process_job_automations`; frequência, última execução/resultado e proprietário. Se inexistentes, registrar a consulta que sustentou a conclusão.
- [ ] **Jobs `update_agent`:** contagens por `queued`, `sent`, `running`, cancelados/expirados e campanhas ativas; separar payload legado incompatível e resultado incerto. Usar consulta administrativa/read-only e não exportar credenciais ou payload bruto.
- [ ] **Backups:** caminho protegido, horário, tamanho, SHA-256, cobertura (DB/config/arquivos/estáticos) e teste de restauração isolada com versão/schema compatíveis; registrar RPO/RTO aplicável.
- [ ] **Estáticos:** owner/grupo, modos e ACLs de `staticfiles`, `vendor`, CSS/JS alterados e sidecars `.gz`/`.br`; confirmar escrita efetiva do usuário `nightowl`, leitura pelo Nginx, espaço livre e lista exata de arquivos a substituir.
- [ ] **Pós-etapa:** checks, smoke HTTP, logs sanitizados, resultado visual autenticado (quando exigido), checkpoint, responsável e decisão GO/NO-GO.

## 11. Pacote de aprovação

Antes de solicitar GO, anexar a esta checklist as evidências coletadas, o relatório de testes do SHA alvo, o backup e restore verificados, as decisões sobre flags e migrations e o registro dos responsáveis/checkpoints. Evidência que ainda dependa de produção ou de Windows real deve permanecer explicitamente como pendente, não ser substituída por teste sintético.

## Referências no repositório

- `config/settings.py`, `config/asgi.py`, `config/wsgi.py`, `requirements.txt`
- `agents/migrations/0047_persistedtelemetryanalysis_and_more.py` até `0050_telemetry_scheduler_health.py`
- `docs/rc47-03-incremento-b.md`, `docs/rc47-03-incremento-c.md`, `docs/rc47-01-hardware-inventory.md`, `docs/rc47-04-incremento-a.md`, `docs/rc47-08-manual-update-channel.md`
- `docs/rc46-04b-automations.md`, `docs/rc46-final-closure-rc47-handoff.md`, `docs/deploy-RC47.md`
