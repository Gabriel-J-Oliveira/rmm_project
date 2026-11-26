# RC47 — Reconciliação de jobs históricos e bloqueio do deploy (17/11/2026)

> **Snapshot histórico de 17/11/2026:** diagnóstico somente leitura concluído; todos os estados e recomendações abaixo exigem nova consulta antes de qualquer decisão. **Nenhum cancelamento, timeout, retry, polling artificial, alteração de produção ou deploy autorizado**. Registrar decisão individual antes de modificar os registros. Ver [handoff RC47](rc47-handoff-operacional-20261117.md).

## Contexto

O preflight do deploy `8467cd9541d5ce93e731a19f1789486abe898402` → `866afe400df532190088d3c5aa678e960f355c2b` identificou três jobs não terminais. A investigação feita pelo Codex em produção comprovou que pertencem ao **registro antigo** de `CS-CVEL-0254` e que estão vencidos.

- Registro antigo: `da5d2b2f-3750-4af5-bd43-6635255fc7b5`, machine_id `CS-CVEL-0254`, domínio CONTROL, agente 0.1.0, offline, último heartbeat em 15/07/2026 13:21 UTC.
- Registro diferente online: `760f9d60-54b1-47b4-81b3-6a350a2db2b4`, machine_id `70bceb8a-ea43-43d9-acd3-7c16025e4cb8`, agente RC45. **Não associar nem transferir jobs por hostname.**
- O protocolo real dos jobs antigos **não foi confirmado** pelos dados persistidos.

## Jobs e decisão proposta

| Job | Situação confirmada | Abordagem segura |
| --- | --- | --- |
| `4ba2c049-6ff6-4174-89d6-5be91aefcccc` | `uninstall_agent` ainda `queued`; criado em 26/10/2026 19:46 UTC por gabriel.oliveira, vencido em 27/10/2026 19:46 UTC, nunca despachado; solicitação `e0ca2746-60ee-444d-88f6-0b337a309837` em `waiting_for_agent` | **Não reenviar**. Com aprovação para encerrar o pedido histórico, usar somente o cancelamento auditado já existente, após confirmar status/UUID novamente |
| `4a5725a1-fef5-4dfd-acc5-564dda113041` | `force_inventory` em `sent`, criado/despachado 09/07, vencido, com evento `job.started`, sem recibo nem resultado terminal | Reconciliador `reconcile_stale_agent_job` dry-run já propôs `timed_out`; executar `--apply` **somente após autorização específica e motivo** |
| `8a5c3619-b955-481e-8f99-0ca871948ca3` | `collect_logs` em `sent`, criado/despachado 09/07, vencido, com evento `job.started`, sem recibo nem resultado terminal | Mesma proposta de timeout auditado, sem afirmar se executou ou não |

O código de entrega do agente examina expiração: o `queued` vencido não deve ser entregue, e os dois `sent` vencidos não são candidatos de retry V2. **Mesmo assim** não houve encerramento de histórico; não inferir que o sistema já os reconciliou. A validade temporal/estado deve ser reavaliada na data da operação.

## Procedimento de governança — sem comandos mutáveis pré-autorizados

1. Confirmar origem, IDs, estado atual, auditoria e identidade por UUID em leitura, sem enviar jobs.
2. Solicitar decisão explícita sobre a solicitação histórica de uninstall (cancelar auditadamente, ou manter e registrar aceitação da inconsistência).
3. Solicitar decisão independente para cada `sent`: timeout administrativo auditado não equivale a prova de falha ou não execução.
4. Depois da autorização, executar **somente** os fluxos suportados pelo código publicado; informar antes/depois, executor, motivo e referência, impedindo mudança em outros UUIDs.
5. Em caso de status mudado, identidade ambígua, falta de fluxo auditado ou erro, interromper. Não editar diretamente SQL nem substituir por cancelamento/timeout improvisado.
6. Revisar novamente a fila sensível antes da janela do deploy e documentar decisão ou risco residual aceito formalmente.

## Estado do deploy relacionado

Até o relatório de 17/11, **NO-GO**: falta confirmar janela de manutenção comunicada, decisão sobre esses jobs e criar checkpoints novos do código, estáticos e PostgreSQL. O código do destino `866afe…` não foi ainda promovido. Nenhum destes jobs deve ser executado ou reconciliado como efeito colateral da publicação.

## Melhorias futuras — não abrir PR nesta fase

Painel administrativo de jobs expirados, resultados inconclusivos e reconciliação; identidade imutável e trilhas de ações; métricas de expiração e risco de reenvio; autorização por tipo de ação. Priorizar após estabilizar RC47/Terminal/Serviços.
