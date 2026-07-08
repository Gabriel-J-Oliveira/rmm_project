# Ticket Workflow MVP

## Status oficiais

O MVP usa os status ja existentes em `tickets.Ticket`:

- `new`: Novo.
- `in_progress`: Em atendimento.
- `waiting_user`: Aguardando solicitante.
- `waiting_third_party`: Aguardando terceiro.
- `resolved`: Resolvido.
- `closed`: Fechado/encerrado.
- `canceled`: Cancelado.

Nao foi criado um status persistido `reopened` nesta fase. Reabertura e registrada por auditoria/evento `ticket_reopened` e o chamado volta para `in_progress`.

## Transicoes permitidas

- `new` -> `in_progress`, `waiting_user`, `waiting_third_party`, `resolved`, `canceled`.
- `in_progress` -> `waiting_user`, `waiting_third_party`, `resolved`, `canceled`.
- `waiting_user` -> `in_progress`, `resolved`, `canceled`.
- `waiting_third_party` -> `in_progress`, `waiting_user`, `resolved`, `canceled`.
- `resolved` -> `closed`, `in_progress`.
- `closed` nao reabre automaticamente nesta versao.
- `canceled` nao reabre nesta versao.

As transicoes para espera, resolucao, encerramento e reabertura exigem motivo ou mensagem publica.

## Acoes tecnicas

- Assumir atendimento atribui `request.user`/ator ao chamado. Se o chamado estiver `new`, muda para `in_progress`, registra auditoria e cria comentario publico simples.
- Aguardar solicitante muda para `waiting_user`, exige mensagem, cria comentario publico, registra auditoria e prepara EmailOutbox `waiting_requester`.
- Resolver exige solucao, muda para `resolved`, cria comentario publico de solucao, registra auditoria e prepara EmailOutbox `ticket_resolved`. O chamado sai da fila operacional padrao, mas permanece consultavel e contestavel pelo solicitante.
- Encerrar so e permitido a partir de `resolved`, muda para `closed`, cria timeline publica de encerramento e registra auditoria. Como ainda nao existe template especifico, a notificacao `ticket_closed` fica registrada como ignorada.
- Reabrir pelo tecnico muda de `resolved` para `in_progress`, exige motivo, registra auditoria, cria comentario publico e prepara EmailOutbox `ticket_reopened`.

## Acoes do solicitante

- Abrir chamado pelo portal cria `new`, origem `portal`, comentario publico de abertura, auditoria e EmailOutbox `ticket_created`.
- Responder chamado aberto cria comentario publico. Se o status era `waiting_user`, volta para `in_progress`.
- Responder chamado resolvido nao e permitido como comentario comum; o usuario deve usar a contestacao/reabertura.
- Contestar chamado resolvido exige motivo, cria comentario publico, muda para `in_progress`, registra auditoria e prepara EmailOutbox `ticket_reopened`.
- Chamados `closed` e `canceled` ficam em modo somente leitura para o solicitante.

## Resolvido x Encerrado

- `resolved` significa finalizado operacionalmente pela equipe tecnica. Nao depende de confirmacao do solicitante, sai da fila padrao e pode ser contestado pelo portal ou por resposta ao e-mail de resolucao.
- `closed` significa encerrado para consulta. Comentario comum, portal e e-mail inbound nao reabrem automaticamente esse status.
- Resposta por e-mail em `resolved` e tratada como contestacao e retorna o chamado para `in_progress`.
- Resposta por e-mail em `closed`/`canceled` e registrada como `skipped`, sem criar comentario ou reabertura automatica.

## Timeline publica

Visivel ao solicitante:

- Chamado aberto.
- Comentarios publicos.
- Aguardando solicitante, resolucao, reabertura e encerramento quando geram comentario publico/evento.
- Anexos publicos vinculados aos comentarios.

Oculto ao solicitante:

- Comentarios internos.
- Auditoria tecnica.
- Erros de e-mail.
- Acoes RMM internas.
- Logs sensiveis.
- Mudancas internas de fila/SLA sem mensagem publica.

## Auditoria interna

O workflow registra `TicketAuditEvent` para:

- `ticket_created`;
- `ticket_assigned`;
- `ticket_status_changed`;
- `ticket_waiting_requester`;
- `ticket_resolved`;
- `ticket_closed`;
- `ticket_reopened`;
- `comment_created`;
- `attachment_created`;
- `notification_prepared` ou `notification_skipped`.

O ator vem de `request.user` quando autenticado, com fallback seguro para a equipe/sistema.

## EmailOutbox

Eventos integrados:

- `ticket_created`;
- `ticket_assigned`;
- `waiting_requester`;
- `ticket_resolved`;
- `ticket_reopened`.

`ticket_closed` ainda nao possui template/aplicacao propria. Nesta fase, a tentativa fica como `notification_skipped` em auditoria para evitar envio duplicado do template de resolucao.

## Implementacao

As regras centrais ficam em `tickets/services/ticket_workflow.py`:

- `can_assume`;
- `can_comment_public`;
- `can_resolve`;
- `can_close`;
- `can_reopen`;
- `assign_ticket`;
- `transition_ticket`;
- `add_ticket_comment`;
- `requester_reply`;
- `requester_reopen`.

As views tecnicas e do solicitante chamam esses helpers para reduzir divergencia entre Central Tecnica, Detalhe Tecnico e Portal do Solicitante.

## Limitacoes

- Nao ha status persistido `reopened`.
- Nao ha template especifico de e-mail para `ticket_closed`.
- Escalonamento continua usando o fluxo existente de fila/responsavel e nao cria status separado.
- Regras de permissao continuam simples: staff/tecnico para area tecnica e solicitante autenticado para `/meus-chamados/`.

## Homologacao

A homologacao funcional do fluxo completo esta documentada em `docs/ticket_workflow_homologation.md`.
