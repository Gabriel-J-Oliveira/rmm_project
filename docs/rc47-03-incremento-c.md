# RC47-03C — Saúde do scheduler de análises

## Escopo e fluxo atual

`process_job_automations` chama `scheduler_round`. A rodada executa automações de Jobs primeiro quando `NIGHTOWL_JOB_AUTOMATIONS_ENABLED` está ativa; a análise persistida é chamada depois, de forma independente e com limite máximo de cinco. A flag de Jobs e a flag de análise são independentes. A suspensão descrita aqui afeta somente a chave `telemetry_analysis` de `AutomationSchedulerHealth`; não pausa nem altera a chave `jobs`.

O repositório versiona o management command, mas não contém cron, timer systemd, Celery beat nem outro invocador recorrente. A frequência real do host precisa ser verificada pelo operador do servidor. Para uma futura ativação, reutilizar o invocador já existente; uma cadência de 15 minutos é suficiente para respeitar o menor backoff de retry. Esta recomendação não configura nem habilita timer algum.

## Saúde, classificação e suspensão

A migration `0050_telemetry_scheduler_health` amplia a linha `telemetry_analysis` existente com tentativa, sucesso operacional, duração, resultado da última rodada, contagens de endpoints processados/bloqueados/em revisão, classe e código sanitizado da falha, streak sistêmico e suspensão/recuperação.

- Falta de janela, endpoint offline, qualidade insuficiente, janela vencida pela retenção e identidade em revisão são resultados por endpoint. Mantêm o retry/backoff existente e não incrementam o streak sistêmico.
- Erros de banco, configuração inválida (incluindo retenção detalhada diferente de sete dias) e falhas inesperadas do scheduler incrementam uma falha por rodada. Falhas em três rodadas consecutivas suspendem a análise e criam evento de auditoria. Uma rodada concluída sem falha sistêmica zera o streak, mesmo que alguns endpoints tenham bloqueios de qualidade.
- Uma rodada interrompida que deixou o estado `running` é contabilizada como falha sistêmica na próxima execução que adquirir o lock.
- No PostgreSQL, lock consultivo de sessão serializa a rodada de telemetria. A concorrência das automações existentes de Jobs permanece sob os locks já usados por elas.
- Dry-run não escreve health, agenda, análise, auditoria ou jobs. Aplicação exige as flags já existentes; ambas continuam desligadas por padrão, dry-run continua ligado e primeira captura continua `unconfigured`.

Suspensão não é removida por uma execução bem-sucedida posterior. Um operador NightOwl técnico com `agents.control_automation` pode consultar `GET /jobs/automations/telemetry-health/` e solicitar recuperação via `POST` no mesmo caminho com `{"action":"resume","reason":"..."}`. O motivo é obrigatório; a ação grava `AuditEvent` com ator e motivo. A recuperação apenas limpa a suspensão; não habilita flags nem muda a política de primeira captura.

## Ativação futura e limites

Não ativar flags nem configurar invocador nesta PR. Antes de um piloto, confirmar o invocador e sua frequência reais no host, backup e aplicação da migration, métricas/alertas para `last_success_at`, estado `suspended`, falhas consecutivas e duração, e procedimento de resposta. Investigar qualquer suspensão, corrigir a causa, validar dry-run e usar recuperação manual auditada; só então considerar habilitar o scheduler em um endpoint/ambiente autorizado.

Primeira captura permanece `unconfigured`; `staggered_24h` continua disponível como escolha futura. O limite de cinco análises por rodada, ciclo de 15 dias, retenção detalhada de sete dias, retries por endpoint, idempotência e bloqueios de identidade ambígua permanecem em vigor.

Na revisão desta branch, a branch independente `codex/rc47-01-hardware-inventory` estava no SHA `4186515ece035861f97868e7d3c273def1639f23` e ainda não continha migration após `0049`. Se RC47-01 adicionar migration antes da integração desta PR, sincronizar e renumerar `0050` para manter uma cadeia linear.
