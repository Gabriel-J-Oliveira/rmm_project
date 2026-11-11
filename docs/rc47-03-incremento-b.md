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

Registrada após implementação: 91 testes de telemetria, persistência, scheduler, automações e módulo PostgreSQL passaram em SQLite (4 testes foram ignorados por exigirem backend/ambiente diferente). O teste novo de dois workers usa `TransactionTestCase` e `SELECT FOR UPDATE SKIP LOCKED` quando disponível. O ambiente local não tem `psql`/`pg_isready` nem serviço PostgreSQL configurado, então a concorrência real PostgreSQL fica para o check CI com serviço PostgreSQL. `manage.py check`, `makemigrations --check --dry-run` e `git diff --check` passaram localmente.

Pendências: escolher política de primeira captura; validar concorrência em PostgreSQL CI; confirmar a causa da ausência histórica de disco/rede no registro real; homologar dry-run e execução controlada em ambiente isolado. Nenhuma flag foi ativada em produção.
