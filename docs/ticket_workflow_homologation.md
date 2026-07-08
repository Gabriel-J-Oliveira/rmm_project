# Homologacao funcional do Workflow MVP

## Metodo

Como o usuario PostgreSQL local nao tem permissao para criar banco de teste, a homologacao foi feita por:

- revisao funcional das views, templates e scripts conectados ao workflow;
- smoke test transacional com rollback usando o banco atual;
- `python manage.py check`;
- `python manage.py makemigrations --check`.

O smoke test criou um chamado temporario dentro de `transaction.atomic()`, executou o ciclo completo e reverteu todos os dados ao final.

## Cenario ponta a ponta validado

Fluxo executado:

1. Solicitante abre chamado com status `new`.
2. Tecnico assume o chamado.
3. Status muda automaticamente para `in_progress`.
4. Tecnico coloca como `waiting_user`.
5. Solicitante responde.
6. Status volta para `in_progress`.
7. Tecnico resolve com solucao publica.
8. Tentativa de comentario comum em `resolved` e bloqueada.
9. Solicitante reabre com motivo.
10. Status volta para `in_progress`.
11. Tecnico resolve novamente.
12. Tecnico encerra.
13. Status final fica `closed`.

Resultado do smoke test:

- comentarios publicos criados: 7;
- eventos de auditoria criados: 20;
- EmailOutbox pendente para:
  - `ticket_assigned`;
  - `waiting_requester`;
  - `ticket_resolved`;
  - `ticket_reopened`;
- evento `ticket_closed` registrado como `notification_skipped`.

## Ajustes realizados na homologacao

- A solucao aplicada no portal agora busca o comentario publico ligado ao evento `ticket_resolved`, evitando que o comentario de encerramento substitua a solucao.
- O escalonamento tecnico agora bloqueia chamados `resolved`, `closed` e `canceled`; eles precisam ser reabertos antes de escalar.
- O preview da Central do Solicitante passou a indicar, para chamados resolvidos, o caminho "Ver detalhes e contestar".
- Chamados `closed` deixaram de oferecer transicao para `in_progress`; encerrado agora e somente leitura sem regra explicita de reabertura automatica.
- A regra de `can_reply` do portal passou a usar `ticket_workflow.can_comment_public()`.
- Evento `ticket_closed` continua sem template de e-mail, mas agora e registrado como notificacao ignorada sem warning operacional.

## Central Tecnica

Validado:

- `new`, `in_progress`, `waiting_user`, `resolved` e `closed` sao refletidos na tabela e no preview.
- Assumir usa `assign_ticket()` e muda `new` para `in_progress`.
- Mudancas sensiveis de status exigem motivo no menu da Central.
- `resolved` nao mostra acao de assumir.
- `closed` e `canceled` exibem modo somente leitura no preview e nao oferecem transicoes de status.
- Erros de transicao retornam JSON amigavel.

Observacao:

- O fluxo de escalonamento continua usando fila/responsavel e nao cria status persistido proprio.

## Central do Solicitante

Validado:

- Cards e filtros usam status reais.
- `waiting_user` fica destacado como "Aguardando voce".
- `resolved` aparece para consulta e oferece caminho para contestar/reabrir pela tela individual.
- `closed` aparece para consulta e fica somente leitura.
- O usuario comum ve apenas chamados filtrados por `requester_email`.

## Tela individual do solicitante

Validado:

- Comentarios internos nao aparecem.
- Anexos publicos aparecem.
- Resolvido exibe solucao aplicada.
- Encerrado fica em modo somente leitura.
- Contestacao/reabertura de `resolved` exige motivo e volta para `in_progress`.
- Encerrado permanece somente leitura.

## Auditoria

Eventos validados no smoke test:

- `ticket_assigned`;
- `ticket_status_changed`;
- `ticket_waiting_requester`;
- `ticket_resolved`;
- `ticket_reopened`;
- `ticket_closed`;
- `comment_created`;
- `notification_prepared`;
- `notification_skipped`.

## EmailOutbox

Eventos com fila real:

- `ticket_assigned`;
- `waiting_requester`;
- `ticket_resolved`;
- `ticket_reopened`.

Evento sem template nesta fase:

- `ticket_closed`: registrado em auditoria como `notification_skipped`.

## Limitacoes restantes

- Ainda nao existe status persistido `reopened`; reabertura e representada por evento `ticket_reopened` e retorno para `in_progress`.
- Ainda nao existe template especifico para `ticket_closed`.
- Notificacao para equipe quando o solicitante responde nao foi ativada por falta de template/roteamento interno definido.
- O smoke test nao substitui suite automatizada completa enquanto o banco de teste PostgreSQL nao puder ser criado.
