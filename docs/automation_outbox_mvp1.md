# Automations MVP 1 e Notification Outbox

## Objetivo

O MVP 1 prepara notificacoes transacionais do Night Owl Desk sem enviar e-mail.
Cada evento suportado cria um registro `NotificationOutbox` com assunto e corpo
ja renderizados a partir de um `DeskTemplate` real.

Nao existe SMTP, worker de envio, recebimento de e-mail ou alteracao automatica
do status da outbox nesta fase.

## Model

`tickets.NotificationOutbox` armazena:

- ticket e template de origem;
- tipo do evento e canal;
- destinatario;
- assunto e corpo renderizados;
- status (`pending`, `sent`, `skipped` ou `failed`);
- datas de criacao e envio;
- metadata operacional.

Os registros podem ser consultados no Django Admin. A administracao e somente
leitura para evitar que uma notificacao seja marcada manualmente como enviada.

## Eventos e templates

| Evento | Template |
| --- | --- |
| `ticket_created` | Confirmacao de chamado criado |
| `ticket_assigned` | Chamado assumido |
| `waiting_requester` | Aguardando solicitante |
| `ticket_resolved` | Chamado resolvido |
| `ticket_reopened` | Chamado reaberto por contestacao |

O servico central e
`tickets.services.automation_outbox.prepare_ticket_notification()`.

## Pontos de disparo

- O drawer rapido prepara `ticket_created`.
- Criacao no modo assumir tambem prepara `ticket_assigned`.
- A primeira atribuicao pela API prepara `ticket_assigned`.
- Alterar o status para aguardando usuario prepara `waiting_requester`.
- Resolver pelo drawer ou pela API de status prepara `ticket_resolved`.
- Alterar um ticket resolvido para um status aberto prepara `ticket_reopened`.

Cada registro gera um `TicketAuditEvent` com a acao
`Notificacao preparada`, incluindo evento, template, destinatario e status.
Essa auditoria aparece no drawer tecnico ja existente no detalhe do chamado.

## Destinatario ausente

O MVP mantem a notificacao como `pending` mesmo quando o chamado ainda nao tem
e-mail do solicitante. A metadata `recipient_missing=true` permite que o futuro
worker aplique a politica de `skipped` sem perder a rastreabilidade do evento.

## Composer

Selecionar uma macro substitui integralmente o texto atual pelo template
renderizado. O texto continua editavel e nenhum comentario e enviado
automaticamente.

## Proxima fase

Um worker futuro devera:

1. selecionar registros `pending`;
2. validar destinatario e configuracao SMTP;
3. enviar o e-mail;
4. marcar `sent`, `skipped` ou `failed`;
5. preencher `sent_at` e registrar auditoria.

## Validacao

```text
python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py seed_desk_mvp2
python manage.py makemigrations --check
```
