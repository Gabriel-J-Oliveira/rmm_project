# RC47-03B — Agendamento automático de análises

## Estado

Implementação preparada na branch `codex/rc47-03-incremento-b`, iniciada em `6090228aed49c08ded2ecd8d4ca24658df9eb3b0`. Não habilita execução recorrente, não grava análise em produção e não altera o agente. A migration `0048_persisted_telemetry_schedule` complementa os modelos da 0047; ela não foi aplicada a nenhum banco operacional nesta etapa.

## Execução e scheduler

O trabalho interno roda dentro da função `scheduler_round` já invocada pelo comando `process_job_automations`. Não cria `AutomationDefinition`, `AutomationOccurrence`, usuário, `AgentJob`, canal, timer ou serviço. A execução automática tem flag própria, `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED` (default `False`) e `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN` (default `True`). A flag de gravação manual de RC47-03A permanece separada e default `False`.

Uma verificação somente leitura pode ser executada com `python manage.py process_job_automations --analysis-dry-run --limit 20`. A opção chama apenas o preview de análise e não despacha jobs, nem grava agenda, análise ou health. Na rodada normal, o scheduler de análises só participa quando sua flag estiver ligada; a flag global de automações continua governando os jobs existentes.

`PersistedTelemetryAnalysisSchedule` guarda a identidade (`endpoint UUID` e snapshot de `machine_id`), vencimento, ciclo, retry/backoff, marca de revisão e até 16 tentativas compactas por endpoint. O processamento usa lock de linha por agenda/endpoint, transação e os recibos idempotentes da 0047. Nenhum job remoto é criado. A rodada recebe limite de 1 a 100 endpoints; cada seleção detalhada continua limitada a 100 janelas.

## Política preparada, ainda sem escolha de produção

- Periodicidade após uma análise aceita: 15 dias por endpoint, ancorada no vencimento do ciclo; interrupções não provocam recuperação em massa, e ciclos vencidos são avançados sem executar análises acumuladas.
- `immediate`: primeira análise na primeira rodada após a agenda ser inicializada.
- `staggered_24h`: primeira rodada distribuída deterministicamente pelo UUID em até 24 horas; a fonte contínua precisa continuar fornecendo novas janelas nesse intervalo.
- `unconfigured` (default): impede qualquer gravação automática e informa o bloqueio. A política de primeira captura segue pendente de decisão operacional; não foi escolhida para produção.

Com política explicitamente escolhida em ambiente isolado, uma falha offline ou de qualidade recebe retries de 15 min, 1 h, 4 h, 12 h e 24 h, no máximo, sem ultrapassar a retenção de sete dias. Depois disso o ciclo é encerrado e o próximo vencimento segue o intervalo de 15 dias. Uma janela válida nunca é reconstruída a partir de rollup.

## Identidade, qualidade e métricas ausentes

A inicialização só considera endpoints ativos com linhas `schema_version=2`, resolução 60 s e dados dentro da retenção. UUID e `machine_id` são preservados e revalidados. `machine_id` duplicado/ausente, hostname+domínio ambíguo ou mudança posterior de identidade marca a agenda `review_required`, sem tentativa de associação por hostname e sem limpeza/reconciliação automática. A verificação usa o mesmo advisory lock transacional do registro de identidade, antes dos locks de endpoint, para impedir uma inserção concorrente entre a checagem e a decisão; esse lock permanece durante a transação limitada da análise. A marca é fail-closed e não é removida automaticamente; não há fluxo automático de liberação após revisão.

O analisador seleciona somente amostras 60 s já recebidas, valida cobertura mínima e métricas primárias e preserva ausências como desconhecidas. A projeção de disco/rede lê apenas chaves `disk.<id>.<métrica>` e `network.<id>.<métrica>` presentes em `metric_summaries`; a análise também preserva `reported_metric_keys` e `source_quality` (incluindo estado dos grupos e metadados de instâncias/interfaces). Assim é possível distinguir ausência de métrica reportada de falha de projeção quando o payload histórico está disponível. Não recebemos nesta execução um payload sanitizado da análise de produção; portanto a causa do disco/rede ausentes naquele registro específico não pode ser atribuída a coleta ou projeção sem inspecionar `reported_metric_keys`, `source_quality` e `metric_status` do mesmo `analysis_id`. Não houve mudança no coletor.

A seleção limita a busca a 100 janelas e continua reportando `limit_reached` quando não encontra nenhuma candidata válida nesse limite. Se uma candidata válida for encontrada, registros adicionais fora do limite não transformam o resultado elegível em falha.

## Limites e rollout

Com até 200 endpoints, o limite padrão de 20 limita a rodada a no máximo 20 inicializações/análises; o volume-alvo após a fase inicial é aproximadamente 13 análises por dia se toda a frota ficar elegível. Cada análise lê no máximo 101 linhas de origem para selecionar até uma candidata; o probe excedente apenas identifica truncamento. PostgreSQL aplica lock timeout de 5 s e statement timeout de 15–20 s. O teste com dois workers reais é necessário no gate PostgreSQL.

Sequência segura: migration 0048 em laboratório; `manage.py check`/dry-run; validar a marca de identidades ambíguas; escolher formalmente a política de primeira captura; habilitar flag somente em ambiente isolado; avaliar histórico, retries e saúde; manter ambas as flags de produção desligadas até autorização separada. A migration cria uma tabela de estado; rollback de código não deve ser tratado como autorização para apagar essa tabela ou qualquer análise já gravada.

## Validação desta branch

Registrada após implementação: 91 testes focados passaram em SQLite (4 testes foram ignorados por exigirem backend/ambiente diferente). O check PostgreSQL do CI também executou o teste transacional de dois workers e passou; o ambiente local não tinha serviço PostgreSQL. `manage.py check`, `makemigrations --check --dry-run` e `git diff --check` passaram localmente.

## Revisão final de integração — 11/11/2026

### Flags e limite combinado

`NIGHTOWL_JOB_AUTOMATIONS_ENABLED` e `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED` são independentes. Só a flag de jobs mantém exatamente o fluxo anterior. Só a flag de análise executa o scheduler de telemetria sem percorrer automações. Com ambas desligadas, a rodada normal falha fechada com `scheduler_disabled`. `--analysis-dry-run` é exceção intencional: sempre faz apenas preview de telemetria, mesmo com ambas desligadas, e não processa jobs nem grava agenda, análise ou health.

Quando ambas estão ligadas, o limite de jobs continua sendo o `--limit` existente (1–100). Para evitar dobrar inadvertidamente o custo da rodada, o trabalho de análise tem teto independente de **5 endpoints por invocação**, ou o menor `--limit` solicitado. Portanto, no caso máximo padrão de `--limit 20`, uma rodada pode processar até 20 definições de jobs mais 5 análises; a telemetria não consome os 20 slots de jobs. A expiração legada de jobs continua com seu limite existente de até 100 registros por rodada. Os testes de automação legados permanecem como regressão.

### Invocação recorrente, saúde e parada

O repositório contém apenas o management command `process_job_automations`; não há cron, unit/timer systemd, Celery beat ou outro invocador recorrente versionado. A frequência e o mecanismo atualmente configurados no servidor não podem ser determinados por inspeção desta branch e precisam ser verificados no host pelo responsável do deploy. O código da análise só grava estado quando as duas condições operacionais de apply estão presentes: scheduler habilitado e `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=false`. Por padrão, a flag está desligada e dry-run está ligado.

O comando termina com erro se a rodada reportar falha, podendo ser usado como sinal por um supervisor externo. Não existe circuit breaker global: retries são limitados por endpoint e por ciclo, enquanto a saúde registra início, último sucesso e contagens da última rodada. Ela não armazena falhas consecutivas nem desativa a flag; tampouco há uma tela de saúde específica para `telemetry_analysis`. Assim, execução recorrente em produção deve continuar desabilitada até haver monitoramento externo definido e uma decisão aprovada para interrupção automática após falhas sistêmicas. Não se criou timer nem mecanismo paralelo nesta PR.

### Primeira captura e estimativa de carga

`staggered_24h` distribui deterministicamente a primeira data de cada UUID em 86.400 segundos. Para uma frota elegível de 100, 200 ou 500 endpoints, a média matemática durante essa primeira janela é respectivamente 100, 200 ou 500 análises/dia; isso não é uma previsão de throughput, pois depende da frequência do invocador. O limite de cinco por rodada requer pelo menos 20, 40 ou 100 invocações para processar essas quantidades, distribuídas ao longo das 24 horas. Em regime, um ciclo a cada 15 dias equivale em média a cerca de 6,7, 13,3 ou 33,3 análises/dia para essas frotas. A cadência real e o tamanho de frota elegível devem ser confirmados antes de qualquer ativação. `unconfigured` continua sendo o padrão; `staggered_24h` é preferência de produto ainda não ativada.

### Identidade ambígua e liberação futura

A agenda permanece por UUID de `AgentMachine` e guarda snapshot de `machine_id`. A implementação não consulta hostname para escolher entre registros, fundir, excluir ou desbloquear registros. Identidade duplicada/missing ou alterada põe a agenda em `review_required`; uma agenda já marcada não é reativada por um ciclo normal. Para uma liberação futura, o operador deve primeiro reconciliar a identidade por evidências independentes (machine_id, histórico de enrollment/heartbeat e autorização), registrar a decisão e o endpoint UUID escolhido; só depois uma ferramenta administrativa auditada poderá limpar a marca e atualizar o snapshot sob lock. Esse fluxo não existe nesta PR e nenhum bypass manual foi adicionado.

### Migration 0048 e sincronização com RC47-07C

A 0048 desta branch depende da 0047 e cria `PersistedTelemetryAnalysisSchedule`. Na consulta de 11/11/2026, não havia branch remota ou PR visível para RC47-07C, portanto sua migration e sua numeração ainda não puderam ser comparadas. Não renumerar a 0048 antes de conhecer a migration concreta.

Sequência recomendada se a RC47-07C integrar primeiro: atualizar refs; mesclar a nova `main` em `codex/rc47-03-incremento-b`; inspecionar o grafo e os nomes de arquivos de migrations; se a 07C tiver ocupado `0048`, renomear **somente a migration desta PR, ainda não aplicada operacionalmente**, para o próximo número livre (provavelmente 0049), ajustar sua dependência para a migration 07C e revisar `showmigrations`/`makemigrations --check`. Em seguida, executar testes de migração desde 0047 e desde a nova migration anterior, incluindo rollback apenas em banco descartável. Nunca reescrever migrations já aplicadas em produção. A sincronização final e o CI serão executados após a 07C chegar à `main`; esta PR permanece sem merge/deploy.

Pendências: primeira captura aguarda decisão formal; frequência e invocador recorrente precisam ser confirmados no servidor; falta um mecanismo aprovado para circuit breaker/alarme de falhas sistêmicas; permanece pendente a inspeção dos metadados da análise de produção para explicar disco/rede ausentes. Nenhuma flag foi ativada em produção.
